"""Trusted browser tools, never a host-side executor for generated Python/JS.

Playwright owns a dedicated event loop (Windows uvicorn may use SelectorEventLoop).
Each conversation has an isolated, in-memory context. No personal profile/CDP port.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

from . import ai_tools
from .ai_settings import redact
from .database import fetch_one, transaction, utc_now


class WaitingForUser(Exception):
    pass


def init_tables(connection):
    connection.executescript('''
      CREATE TABLE IF NOT EXISTS ai_browser_state (
        thread_id INTEGER PRIMARY KEY REFERENCES ai_threads(id),
        status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
        observation TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS ai_browser_rows (
        thread_id INTEGER PRIMARY KEY REFERENCES ai_threads(id),
        fields TEXT NOT NULL, records TEXT NOT NULL, updated_at TEXT NOT NULL,
        unique_by TEXT NOT NULL DEFAULT '[]');
      CREATE TABLE IF NOT EXISTS ai_browser_sites (
        thread_id INTEGER NOT NULL REFERENCES ai_threads(id), host TEXT NOT NULL,
        PRIMARY KEY(thread_id,host));
      CREATE TABLE IF NOT EXISTS ai_browser_row_backup (
        thread_id INTEGER PRIMARY KEY REFERENCES ai_threads(id),
        records TEXT NOT NULL, updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS ai_browser_row_filters (
        thread_id INTEGER PRIMARY KEY REFERENCES ai_threads(id),
        filters TEXT NOT NULL);
    ''')
    if 'unique_by' not in {row['name'] for row in connection.execute('PRAGMA table_info(ai_browser_rows)')}:
        connection.execute("ALTER TABLE ai_browser_rows ADD COLUMN unique_by TEXT NOT NULL DEFAULT '[]'")
    if 'filters' not in {row['name'] for row in connection.execute('PRAGMA table_info(ai_browser_row_backup)')}:
        connection.execute("ALTER TABLE ai_browser_row_backup ADD COLUMN filters TEXT NOT NULL DEFAULT '{}'")
    connection.execute("UPDATE ai_browser_state SET status='closed',message='服务已重启，浏览器会话或采集快照已结束；已提取数据保留。' WHERE status IN ('open','waiting_user','native')")


def remember(thread_id, status, message='', observation=None):
    with transaction() as connection:
        old = connection.execute('SELECT observation FROM ai_browser_state WHERE thread_id=?', (thread_id,)).fetchone()
        if observation is not None:
            prior = json.loads(old['observation']) if old else {}
            visited = list(prior.get('observed_urls', []))
            url = safe_url(observation.get('url', ''))[:500]
            if url and url not in visited:
                visited.append(url)
            observation = {**observation, 'observed_urls': visited[-40:]}
        content = json.dumps(observation, ensure_ascii=False) if observation is not None else (old['observation'] if old else '{}')
        connection.execute('''INSERT INTO ai_browser_state VALUES(?,?,?,?,?)
          ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status,message=excluded.message,
          observation=excluded.observation,updated_at=excluded.updated_at''',
          (thread_id, status, redact(message)[:1000], content, utc_now()))


def state(thread_id, *, evidence=False):
    item = fetch_one('SELECT * FROM ai_browser_state WHERE thread_id=?', (thread_id,))
    rows = fetch_one('SELECT records,fields,unique_by FROM ai_browser_rows WHERE thread_id=?', (thread_id,))
    if item:
        observed = json.loads(item.pop('observation'))
        item['url'] = observed.get('url', '')
        if evidence:
            item['last_observation'] = observed
            item['observed_urls_note'] = '这些URL只证明访问过，不代表已完成提取；结合已保存字段、规则和结果继续，不重复从第一页重新开始。'
    result = {**(item or {'status': 'closed', 'message': ''}),
              'row_count': len(json.loads(rows['records'])) if rows else 0}
    if rows and evidence:
        result.update(fields=json.loads(rows['fields']), unique_by=json.loads(rows['unique_by']),
                      sample=json.loads(rows['records'])[:3])
        saved_filter = fetch_one('SELECT filters FROM ai_browser_row_filters WHERE thread_id=?', (thread_id,))
        result['filters'] = json.loads(saved_filter['filters']) if saved_filter else {}
    return result


def discovered_hosts(thread_id):
    from .database import fetch_all
    return {row['host'] for row in fetch_all('SELECT host FROM ai_browser_sites WHERE thread_id=?', (thread_id,))}


def append_rows(thread_id, fields, records, *, unique_by=None, limit=2000):
    unique_by = unique_by or [name for name in fields if name != '_source_url']
    if not isinstance(unique_by, list) or not unique_by or any(name not in fields or name == '_source_url' for name in unique_by):
        raise ValueError('unique_by 必须是提取字段名的非空 JSON 数组，不包含来源字段')
    if type(limit) is not int or not 1 <= limit <= 2000:
        raise ValueError('limit 为 1–2000 的累计目标上限')
    def identity(row):
        if any(not row.get(name, '').strip() for name in unique_by):
            raise ValueError('去重字段存在空值，请调整选择器；已有结果保留')
        return json.dumps([row[name] for name in unique_by], ensure_ascii=False)
    with transaction() as connection:
        previous = connection.execute('SELECT * FROM ai_browser_rows WHERE thread_id=?', (thread_id,)).fetchone()
        if previous and set(json.loads(previous['fields'])) != set(fields):
            raise ValueError('已有结果的字段不同；请沿用字段：' + previous['fields'])
        if previous:
            fields = json.loads(previous['fields'])
        if previous and json.loads(previous['unique_by']) != unique_by:
            raise ValueError('已有结果的去重字段不同；请沿用原主键或使用新对话')
        result = json.loads(previous['records']) if previous else []
        saved_filter = connection.execute('SELECT filters FROM ai_browser_row_filters WHERE thread_id=?', (thread_id,)).fetchone()
        rules = json.loads(saved_filter['filters']) if saved_filter else {}
        records = filter_records(records, fields, rules)
        if len(result) > limit:
            raise ValueError('已有结果超过本次上限，请使用新对话采集不同数量')
        known = {identity(row): row for row in result}
        enriched = 0
        for row in records:
            key = identity(row)
            if key in known:
                existing = known[key]
                for name in fields:
                    if not existing.get(name, '').strip() and row.get(name, '').strip():
                        existing[name] = row[name]
                        enriched += 1
                continue
            if len(result) >= limit:
                continue
            known[key] = row
            result.append(row)
        encoded = json.dumps(result, ensure_ascii=False)
        if len(result) > 2000 or len(encoded.encode()) > 8 * 1024 * 1024:
            raise ValueError('浏览器提取上限为 2000 条、8MB；已有结果保留')
        connection.execute('''INSERT INTO ai_browser_rows VALUES(?,?,?,?,?) ON CONFLICT(thread_id)
          DO UPDATE SET records=excluded.records,updated_at=excluded.updated_at''',
          (thread_id, json.dumps(fields), encoded, utc_now(), json.dumps(unique_by)))
    return {'row_count': len(result), 'sample': result[:5], 'enriched_fields': enriched,
            'download_url': f'/api/ai/threads/{thread_id}/browser/results.csv',
            'unique_by': unique_by, 'limit': limit,
            'filters': rules,
            'validation': '真实页面提取，按声明主键去重；业务条件仍需核对'}


def filter_records(records, fields, rules):
    from .ai_scraping import extract_json
    if not isinstance(rules, dict) or any(k not in fields for k in rules):
        raise ValueError('filters 只能使用已有字段')
    for key, rule in rules.items():
        # Validate even if no records remain; preserve original full rows and values.
        args = {'rows': 'rows', 'fields': '{"value":"value","index":"index"}',
                'filters': json.dumps({'value': rule})}
        extract_json({'rows': []}, args)
        retained = []
        for start in range(0, len(records), 500):
            batch = records[start:start + 500]
            mapped = [{'value': row.get(key, ''), 'index': i} for i, row in enumerate(batch)]
            _, kept = extract_json({'rows': mapped}, args)
            retained.extend(batch[int(row['index'])] for row in kept)
        records = retained
    return records


def refine_rows(thread_id, filters, restore=False):
    """Filter persisted evidence with a one-step undo, never manufacture records."""
    with transaction() as connection:
        item = connection.execute('SELECT * FROM ai_browser_rows WHERE thread_id=?', (thread_id,)).fetchone()
        if not item:
            raise ValueError('尚未提取数据')
        original = json.loads(item['records'])
        previous_filter = connection.execute('SELECT filters FROM ai_browser_row_filters WHERE thread_id=?', (thread_id,)).fetchone()
        previous_rules = previous_filter['filters'] if previous_filter else '{}'
        if restore:
            backup = connection.execute('SELECT records,filters FROM ai_browser_row_backup WHERE thread_id=?', (thread_id,)).fetchone()
            if not backup:
                raise ValueError('没有可恢复的筛选前结果')
            records = json.loads(backup['records'])
            rules = json.loads(backup['filters'])
        else:
            rules = json.loads(filters)
            fields = json.loads(item['fields'])
            if not isinstance(rules, dict) or not rules or any(k not in fields for k in rules):
                raise ValueError('filters 需要已有字段的非空筛选规则')
            records = filter_records(original, fields, rules)
        connection.execute('''INSERT INTO ai_browser_row_backup(thread_id,records,updated_at,filters) VALUES(?,?,?,?) ON CONFLICT(thread_id)
            DO UPDATE SET records=excluded.records,updated_at=excluded.updated_at,filters=excluded.filters''',
            (thread_id, item['records'], utc_now(), previous_rules))
        connection.execute('''INSERT INTO ai_browser_row_filters VALUES(?,?) ON CONFLICT(thread_id)
            DO UPDATE SET filters=excluded.filters''', (thread_id, json.dumps(rules, ensure_ascii=False)))
        connection.execute('UPDATE ai_browser_rows SET records=?,updated_at=? WHERE thread_id=?',
            (json.dumps(records, ensure_ascii=False), utc_now(), thread_id))
    return {'row_count': len(records), 'previous_count': len(original), 'sample': records[:5],
            'filters': rules,
            'undo_available': True, 'download_url': f'/api/ai/threads/{thread_id}/browser/results.csv'}


def export_csv(thread_id):
    item = fetch_one('SELECT * FROM ai_browser_rows WHERE thread_id=?', (thread_id,))
    if not item:
        raise ValueError('尚未提取数据')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=json.loads(item['fields']))
    writer.writeheader()
    # Prevent a page-controlled cell from executing a spreadsheet formula.
    for row in json.loads(item['records']):
        writer.writerow({key: "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
                         for key, value in row.items()})
    return stream.getvalue().encode('utf-8-sig')


def safe_url(url):
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not re.search(r'token|secret|password|cookie|session|auth|code|key|signature', k, re.I)]
    return urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, urlencode(query), ''))


async def check_public(url):
    host = urlsplit(url).hostname or ''
    await asyncio.to_thread(ai_tools.public_target, url, {host.lower().rstrip('.')})


OBSERVE = r'''() => {
  const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  document.querySelectorAll('[data-spiderfly-ref]').forEach(e => e.removeAttribute('data-spiderfly-ref'));
  const stamp = Math.random().toString(36).slice(2,10);
  const nodes = [...document.querySelectorAll('a,button,input,textarea,select,[role="button"],[role="link"]')].filter(visible).slice(0,100);
  const elements = nodes.map((e,i) => {
    const ref = stamp+'-'+i; e.setAttribute('data-spiderfly-ref',ref);
    return {ref,tag:e.tagName.toLowerCase(),type:e.getAttribute('type')||'',
      text:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('placeholder')||'').slice(0,160),
      href:e.tagName==='A'?e.href:undefined};
  });
  return {title:document.title,text:(document.body?.innerText||'').slice(0,12000),elements};
}'''


@dataclass
class Session:
    browser: object
    context: object
    page: object
    touched: float = field(default_factory=time.monotonic)
    refs: set = field(default_factory=set)
    observed_url: str = ''
    network: list = field(default_factory=list)
    paused: bool = False
    native_engine: object = None
    api_responses: dict = field(default_factory=dict)
    next_response_id: int = 0
    navigation_status: int = 0


class BrowserService:
    def __init__(self, *, headless=False, channel=None):
        self.headless = headless
        self.channel = channel
        self.sessions = {}
        self.snapshots = {}
        self.engine = None
        self.lock = asyncio.Lock()

    async def close(self, thread_id, message='浏览器已关闭，已提取数据保留。'):
        self.snapshots.pop(thread_id, None)
        session = self.sessions.pop(thread_id, None)
        if session:
            if session.native_engine:
                await session.native_engine.close()
            else:
                try:
                    await session.context.close()
                finally:
                    await session.browser.close()
        remember(thread_id, 'closed', message)
        with transaction() as connection:
            connection.execute("UPDATE ai_turns SET status='interrupted',error=?,ended_at=? WHERE thread_id=? AND status='waiting_user'",
                               (message, utc_now(), thread_id))

    async def shutdown(self):
        self.snapshots.clear()
        for thread_id in list(self.sessions):
            await self.close(thread_id)
        if self.engine:
            await self.engine.stop()
            self.engine = None

    async def expire(self):
        for key, (_, touched) in list(self.snapshots.items()):
            if time.monotonic() - touched > 1800:
                self.snapshots.pop(key, None)
                if key not in self.sessions:
                    remember(key, 'closed', '采集快照已过期；已提取结果保留。')
        for key, session in list(self.sessions.items()):
            if time.monotonic() - session.touched > 1800:
                await self.close(key, '浏览器闲置超过 30 分钟，已关闭；已提取数据保留。')

    async def create(self, thread_id, *, native=False):
        await self.expire()
        if len(self.sessions) >= 3:
            raise ValueError('已有 3 个浏览器会话，请先关闭不用的会话')
        if not self.engine:
            try:
                from playwright.async_api import async_playwright
                self.engine = await async_playwright().start()
            except ImportError:
                raise ValueError('浏览器环境未安装：请在服务环境安装 backend/requirements-browser.txt') from None
        options = {'headless': self.headless}
        if self.channel:
            options['channel'] = self.channel
        native_engine = None
        try:
            if native:
                from scrapling.fetchers import AsyncStealthySession
                native_engine = AsyncStealthySession(headless=True,
                    executable_path=self.engine.chromium.executable_path,
                    solve_cloudflare=False, google_search=False, retries=1,
                    additional_args={'accept_downloads': False, 'service_workers': 'block'})
                await native_engine.start()
                browser = native_engine
            else:
                browser = await self.engine.chromium.launch(**options)
        except Exception:
            raise ValueError('无法启动探索浏览器：请安装 Chromium（python -m playwright install chromium），并确保服务运行在可交互的 Windows 桌面；可配置 SPIDERFLY_BROWSER_CHANNEL=msedge') from None
        try:
            context = native_engine.context if native_engine else await browser.new_context(accept_downloads=False, service_workers='block')
            context.set_default_timeout(10000)
            context.set_default_navigation_timeout(20000)
            await context.route_web_socket('**/*', lambda route: route.close())
            session = Session(browser, context, None, native_engine=native_engine)

            async def route_request(route):
                try:
                    await check_public(route.request.url)
                    await route.continue_()
                except Exception:
                    session.network.append({'url': safe_url(route.request.url), 'blocked': '非公开地址或地址检查失败'})
                    session.network[:] = session.network[-60:]
                    await route.abort()

            async def response(reply):
                entry = {'url': safe_url(reply.url), 'status': reply.status,
                         'method': reply.request.method, 'type': reply.request.resource_type}
                session.network.append(entry)
                session.network[:] = session.network[-60:]
                if native and reply.request.resource_type in {'xhr', 'fetch'}:
                    from .ai_scraping import response_evidence
                    def capture(data):
                        session.next_response_id += 1
                        key = str(session.next_response_id)
                        session.api_responses[key] = {'data': data, 'url': safe_url(reply.url),
                            'page_url': safe_url(session.page.url), 'request_url': reply.url,
                            'method': reply.request.method, 'body': reply.request.post_data or '',
                            'content_type': reply.request.headers.get('content-type', '')}
                        while len(session.api_responses) > 12:
                            session.api_responses.pop(next(iter(session.api_responses)))
                        return key
                    entry.update(await response_evidence(reply, capture))

            await context.route('**/*', route_request)
            context.on('response', response)
            session.page = await context.new_page()
            self.sessions[thread_id] = session
            remember(thread_id, 'open', '已创建独立浏览器会话。', {})
            return session
        except BaseException:
            await browser.close()
            raise

    async def observe(self, thread_id, session):
        pages = [page for page in session.context.pages if not page.is_closed()]
        if not pages:
            await self.close(thread_id)
            raise ValueError('浏览器窗口已关闭，请重新打开')
        if session.page.is_closed():
            session.page = pages[-1]
        result = await session.page.evaluate(OBSERVE)
        parser = ai_tools.PageText()
        html = await session.page.content()
        if len(html.encode()) <= 4 * 1024 * 1024:
            parser.feed(html)
            result['structure'] = '\n'.join(parser.structure)[:18000]
            result['structure'] = re.sub(r'https?://[^\s"<>]+', lambda m: safe_url(m.group()), result['structure'])
            if session.native_engine:
                self.snapshots[thread_id] = ({'html': html, 'url': session.page.url,
                    'engine': 'AsyncStealthySession', 'status': session.navigation_status, 'trace': [], 'live': True}, time.monotonic())
        result = json.loads(redact(json.dumps(result, ensure_ascii=False)))
        session.refs = {item['ref'] for item in result['elements']}
        session.observed_url = session.page.url
        result.update(url=safe_url(session.page.url), tabs=[{'index': i, 'url': safe_url(page.url)} for i, page in enumerate(pages)],
                      network=session.network[-12:])
        for item in result['elements']:
            if item.get('href'):
                item['href'] = safe_url(item['href'])
        if session.native_engine:
            result.update(engine='AsyncStealthySession', live=True)
        remember(thread_id, 'native' if session.native_engine else 'open', observation=result)
        urls = [session.page.url] + [row['url'] for row in session.network if row.get('status')]
        with transaction() as connection:
            for url in urls:
                parsed = urlsplit(url)
                if parsed.scheme in {'http', 'https'} and parsed.hostname:
                    connection.execute('INSERT OR IGNORE INTO ai_browser_sites VALUES(?,?)', (thread_id, parsed.hostname.lower().rstrip('.')))
        return result

    async def run(self, thread_id, name, args):
        async with self.lock:
            await self.expire()
            if name == 'browser_refine_results':
                if args['action'] not in {'filter', 'undo'}:
                    raise ValueError('action 仅支持 filter 或 undo')
                return refine_rows(thread_id, args['filters'], args['action'] == 'undo')
            if name.startswith('scrape_'):
                return await self.native(thread_id, name, args)
            if name == 'browser_close':
                await self.close(thread_id)
                return {'status': 'closed'}
            session = self.sessions.get(thread_id)
            if name in {'browser_open', 'browser_search'}:
                if session and session.page.is_closed():
                    pages = [page for page in session.context.pages if not page.is_closed()]
                    if pages:
                        session.page = pages[-1]
                    else:
                        await self.close(thread_id)
                        session = None
                url = args['url'] if name == 'browser_open' else 'https://www.bing.com/search?q=' + quote(args['query'][:500])
                await check_public(url)
                if session is None:
                    session = await self.create(thread_id)
                if session.paused:
                    raise ValueError('等待人工处理；请在页面点击“我已处理，继续”')
                session.refs.clear()
                response = await session.page.goto(url, wait_until='domcontentloaded')
                session.navigation_status = response.status if response else 0
                if session.native_engine:
                    await session.page.wait_for_timeout(2000)
            elif session is None:
                raise ValueError('没有可用浏览器会话（可能已关闭或过期），请先 browser_open 或 browser_search')
            session.touched = time.monotonic()
            if name == 'browser_resume':
                if not session.paused:
                    raise ValueError('浏览器当前没有等待人工处理')
                session.paused = False
                return await self.observe(thread_id, session)
            if session.paused:
                raise ValueError('等待人工处理；请在页面点击“我已处理，继续”')
            if name == 'browser_wait_user':
                if session.native_engine:
                    raise ValueError('当前为原生无头会话，不能人工操作；确需人工时先 browser_close，再 browser_open 创建可见窗口。')
                await self.observe(thread_id, session)
                session.paused = True
                await session.page.bring_to_front()
                reason = args['reason'][:600]
                remember(thread_id, 'waiting_user', reason + ' 请在宿主机浏览器完成后点击“我已处理，继续”。会话闲置 30 分钟后关闭。')
                return {'status': 'waiting_user', 'reason': reason}
            if name == 'browser_act':
                action, ref, value = args['action'], args['ref'], args['value']
                if action == 'switch_tab':
                    pages = [page for page in session.context.pages if not page.is_closed()]
                    if not value.isdigit() or int(value) >= len(pages):
                        raise ValueError('页签编号无效，请重新观察')
                    session.page = pages[int(value)]
                elif action == 'scroll':
                    if value not in {'up', 'down'}:
                        raise ValueError('滚动方向为 up/down')
                    await session.page.mouse.wheel(0, 700 if value == 'down' else -700)
                elif action == 'wait':
                    await session.page.wait_for_timeout(1000)
                elif action in {'click', 'fill', 'press', 'select'}:
                    if ref not in session.refs or session.page.url != session.observed_url:
                        raise ValueError('元素引用已过期，请 browser_observe 后使用新 ref')
                    locator = session.page.locator(f'[data-spiderfly-ref="{ref}"]')
                    if await locator.count() != 1:
                        raise ValueError('元素已变化，请重新观察')
                    if action == 'fill':
                        if await locator.get_attribute('type') == 'password' or len(value) > 1000:
                            raise ValueError('密码由用户直接在浏览器输入；普通输入最多 1000 字')
                        await locator.fill(value)
                    elif action == 'press':
                        if value not in {'Enter', 'Tab', 'Escape', 'ArrowDown', 'ArrowUp'}:
                            raise ValueError('不支持的按键')
                        await locator.press(value)
                    elif action == 'select':
                        await locator.select_option(label=value)
                    else:
                        await locator.click()
                else:
                    raise ValueError('不支持的浏览器动作')
            if name == 'browser_network':
                return {'requests': session.network, 'note': '仅请求元数据；不输出请求头、Cookie、请求体或响应凭据。'}
            if name == 'browser_extract':
                return await self.extract(thread_id, session, args)
            return await self.observe(thread_id, session)

    async def native(self, thread_id, name, args):
        from . import ai_scraping
        from types import SimpleNamespace
        if name == 'scrape_collect_options':
            session = self.sessions.get(thread_id)
            if not session or not session.native_engine or session.paused:
                raise ValueError('请先建立可操作的原生持久会话')
            captured = session.api_responses.get(args['response_id'])
            if not captured:
                raise ValueError('响应已过期，请先重新观察真实搜索响应')
            parameters = ai_scraping.json_evidence(captured['data'], captured['body']).get('parameters', {})
            page_key = next((key for key in parameters if key.lower() in {'page', 'pagesize', 'offset', 'limit'}), None)
            if page_key is None:
                raise ValueError('批量筛选需要已观察且带分页字段的只读列表响应')
            # Validate the observed endpoint without issuing any guessed request.
            ai_scraping.prepare_replay(captured, {page_key: int(parameters[page_key])})
            values = json.loads(args['values'])
            if not isinstance(values, list) or not 1 <= len(values) <= 20 or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in values) or len(set(values)) != len(values):
                raise ValueError('values 为1–20个不同的已观察选项文字')
            for key in ('menu', 'options', 'dismiss'):
                if len(args[key]) > 500:
                    raise ValueError('选择器最长500字')
            page = session.page
            origin = urlsplit(captured['request_url'])
            await page.locator(args['menu']).hover(timeout=5000)
            options = page.locator(args['options'])
            observed = [value.strip() for value in await options.all_text_contents()]
            if args['reset_text'] not in observed or any(value not in observed or value == args['reset_text'] for value in values):
                raise ValueError('选项或重置文字不在当前菜单中，请重新观察')
            # Validate extraction rules before performing any batch operation.
            ai_scraping.extract_json(captured['data'], args, base_url=captured['page_url'])
            self.snapshots.pop(thread_id, None)
            completed, started = [], time.monotonic()
            result = {'row_count': state(thread_id)['row_count']}
            error = ''
            for value in values:
                active = fetch_one("SELECT stop_requested FROM ai_turns WHERE thread_id=? AND status='running' ORDER BY id DESC LIMIT 1", (thread_id,))
                if active and active['stop_requested']:
                    error = '用户已停止，已保存进度'
                    break
                if time.monotonic() - started >= 110:
                    error = '本批达到110秒，已保存进度，可继续remaining'
                    break
                try:
                    if args['dismiss']:
                        dismiss = page.locator(args['dismiss'])
                        if await dismiss.count() == 1 and await dismiss.is_visible():
                            await dismiss.click(timeout=5000)
                    await page.locator(args['menu']).hover(timeout=5000)
                    await options.filter(has_text=re.compile(r'^\s*' + re.escape(args['reset_text']) + r'\s*$')).click(timeout=5000)
                    await page.wait_for_timeout(1000)
                    await page.locator(args['menu']).hover(timeout=5000)
                    initiated = set()
                    def track(request):
                        initiated.add(request)
                    def matches(reply):
                        parsed = urlsplit(reply.url)
                        return reply.request in initiated and parsed.hostname == origin.hostname and parsed.path == origin.path
                    page.on('request', track)
                    try:
                        async with page.expect_response(matches, timeout=10000) as pending:
                            await options.filter(has_text=re.compile(r'^\s*' + re.escape(value) + r'\s*$')).click(timeout=5000)
                        reply = await pending.value
                    finally:
                        page.remove_listener('request', track)
                    if reply.status >= 400 or int(reply.headers.get('content-length', '0')) > 2 * 1024 * 1024:
                        raise ValueError('本次列表响应受限或超过2MB')
                    body = await asyncio.wait_for(reply.body(), timeout=10)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError('本次列表响应超过2MB')
                    data = json.loads(body)
                    if isinstance(data, dict) and data.get('code') not in (None, 0, 200, '0', '200'):
                        raise ValueError('列表接口返回需核对的 code=' + str(data['code'])[:40])
                    actual = ai_scraping.json_evidence(data, reply.request.post_data or '').get('parameters', {})
                    for key in ('city', 'query', 'keyword'):
                        if key in parameters and actual.get(key) != parameters[key]:
                            raise ValueError('实际搜索城市或关键词发生变化，未保存本批结果')
                    fields, records = ai_scraping.extract_json(data, args, base_url=page.url)
                    for row in records:
                        row['_source_url'] = safe_url(page.url)
                    result = append_rows(thread_id, fields + ['_source_url'], records,
                        unique_by=json.loads(args['unique_by']), limit=int(args['limit']))
                    completed.append({'option': value, 'matched': len(records), 'row_count': result['row_count']})
                    session.touched = time.monotonic()
                    remember(thread_id, 'native', '原生批量筛选中，结果已保存。',
                        {'url': safe_url(page.url), 'engine': 'AsyncStealthySession', 'batch_progress': completed[-5:]})
                    if result['row_count'] >= int(args['limit']):
                        break
                    await page.wait_for_timeout(1000)
                except Exception as exc:
                    error = redact(str(exc))[:500] if isinstance(exc, ValueError) else operation_error_hint(exc)
                    break
            try:
                html = await page.content()
                if len(html.encode()) <= 4 * 1024 * 1024:
                    snapshot = {'html': html, 'url': page.url, 'engine': 'AsyncStealthySession',
                        'status': session.navigation_status, 'trace': [], 'live': True}
                    self.snapshots[thread_id] = (snapshot, time.monotonic())
                    observed = ai_scraping.inspect(snapshot)
                    observed.update(network=session.network[-12:], batch_progress=completed[-5:],
                        batch_remaining=values[len(completed):], batch_error=error)
                    remember(thread_id, 'native', '原生批量筛选已保存结果。', observed)
            except Exception as exc:
                error = error or ('结果已保存，最新页面观察失败：' + operation_error_hint(exc))
            return {**result, 'completed': completed, 'remaining': values[len(completed):], 'error': error,
                    'applied_filters': json.loads(args['filters']),
                    'note': '仅已完成选项的数据已保存；remaining未完成，不能宣称全部采完。'}
        if name == 'scrape_api_page':
            session = self.sessions.get(thread_id)
            captured = session.api_responses.get(args['response_id']) if session else None
            if not captured or not session.native_engine:
                raise ValueError('请使用本对话原生会话中已观察的列表响应编号')
            request = ai_scraping.prepare_replay(captured, json.loads(args['pagination']))
            await check_public(request['url'])
            reply = await session.page.evaluate('''async r => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 20000);
                try {
                const response = await fetch(r.url, {method:r.method, credentials:'same-origin',
                    headers:r.method==='POST'?{'content-type':r.content_type}:undefined,
                    body:r.method==='POST'?r.body:undefined, signal:controller.signal});
                if (Number(response.headers.get('content-length')) > 2097152) throw new Error('响应超过2MB');
                const text = await response.text();
                if (new TextEncoder().encode(text).length > 2097152) throw new Error('响应超过2MB');
                return {status:response.status, data:JSON.parse(text)};
                } finally { clearTimeout(timer); }
            }''', request, isolated_context=False)
            session.next_response_id += 1
            key = str(session.next_response_id)
            session.api_responses[key] = {**captured, 'data': reply['data'], 'body': request['body'],
                'request_url': request['url'], 'url': safe_url(request['url'])}
            while len(session.api_responses) > 12:
                session.api_responses.pop(next(iter(session.api_responses)))
            session.touched = time.monotonic()
            evidence = ai_scraping.json_evidence(reply['data'], request['body'])
            evidence.update(response_id=key, status=reply['status'], url=safe_url(request['url']))
            return evidence
        if name == 'scrape_json_extract':
            session = self.sessions.get(thread_id)
            captured = session.api_responses.get(args['response_id']) if session else None
            if not captured:
                raise ValueError('响应已过期或不属于本对话；请在原生会话重新观察接口')
            fields, records = ai_scraping.extract_json(captured['data'], args, base_url=captured['page_url'])
            for row in records:
                row['_source_url'] = captured['page_url']
            return append_rows(thread_id, fields + ['_source_url'], records,
                unique_by=json.loads(args['unique_by']), limit=int(args['limit']))
        if name in {'scrape_live_open', 'scrape_live_act'}:
            session = self.sessions.get(thread_id)
            if name == 'scrape_live_open':
                await check_public(args['url'])
                if session and not session.native_engine:
                    await self.close(thread_id)
                    session = None
                if session is None:
                    session = await self.create(thread_id, native=True)
                reply = await session.page.goto(args['url'], wait_until='domcontentloaded')
                await session.page.wait_for_timeout(2000)
                status = reply.status if reply else 0
                session.navigation_status = status
            else:
                if not session or not session.native_engine or session.page.is_closed():
                    raise ValueError('请先 scrape_live_open 建立原生持久会话')
                await ai_scraping.live_action(session.page, args)
                status = self.snapshots.get(thread_id, ({}, 0))[0].get('status', 0)
            session.touched = time.monotonic()
            result = {'html': await session.page.content(), 'url': session.page.url,
                      'engine': 'AsyncStealthySession', 'status': status, 'trace': [], 'live': True}
            if len(result['html'].encode()) > 4 * 1024 * 1024:
                raise ValueError('页面超过 4MB，请缩小范围')
            self.snapshots[thread_id] = (result, session.touched)
            observed = ai_scraping.inspect(result)
            observed['network'] = session.network[-12:]
            observed['live'] = True
            remember(thread_id, 'native', '原生 Stealthy 持久会话，可后台筛选和滚动。', observed)
            with transaction() as connection:
                connection.execute('INSERT OR IGNORE INTO ai_browser_sites VALUES(?,?)',
                    (thread_id, (urlsplit(session.page.url).hostname or '').lower().rstrip('.')))
            return observed
        if name in {'scrape_search', 'scrape_page'}:
            if thread_id not in self.snapshots and len(self.snapshots) >= 8:
                raise ValueError('已有 8 个采集快照，请关闭不用的会话后继续')
            url = args['url'] if name == 'scrape_page' else 'https://www.bing.com/search?q=' + quote(args['query'][:500])
            await check_public(url)
            result = await asyncio.to_thread(ai_scraping.request, url, args.get('mode') or 'auto', args.get('wait_for') or '')
            self.snapshots[thread_id] = (result, time.monotonic())
            observed = ai_scraping.inspect(result)
            remember(thread_id, 'native', f"原生 {result['engine']}，后台采集，无可见窗口。", observed)
            with transaction() as connection:
                hostname = (urlsplit(result['url']).hostname or '').lower().rstrip('.')
                connection.execute('INSERT OR IGNORE INTO ai_browser_sites VALUES(?,?)', (thread_id, hostname))
            return observed
        if thread_id not in self.snapshots:
            raise ValueError('没有可用采集快照，请先 scrape_page')
        result, _ = self.snapshots[thread_id]
        self.snapshots[thread_id] = (result, time.monotonic())
        if name == 'scrape_inspect':
            return ai_scraping.inspect(result, args['selector'])
        if name == 'scrape_extract':
            return await self.extract(thread_id, SimpleNamespace(page=ai_scraping.SnapshotPage(result)), args)
        raise ValueError('不支持的原生采集工具')

    async def extract(self, thread_id, session, args):
        fields = json.loads(args['fields'])
        if not isinstance(fields, dict) or not 1 <= len(fields) <= 20 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()):
            raise ValueError('fields 为字段名到 Scrapling CSS 选择器的 JSON 对象，最多 20 个字段')
        if '_source_url' in fields:
            raise ValueError('_source_url 为平台保留字段')
        if any(not (v.endswith('::text') or v.endswith('::attr(href)')) for v in fields.values()):
            raise ValueError('字段选择器必须以 ::text 或 ::attr(href) 结尾，不提取任意 HTML 或凭据属性')
        from scrapling import Selector
        html = await session.page.content()
        if len(html.encode()) > 4 * 1024 * 1024:
            raise ValueError('页面超过 4MB，请缩小页面范围')
        page = Selector(content=html, url=session.page.url)
        rows = page.css(args['rows'])
        if len(rows) > 500:
            raise ValueError('单页超过 500 个匹配，请缩小记录选择器')
        from urllib.parse import urljoin
        result = []
        for row in rows:
            item = {}
            for key, selector in fields.items():
                value = ' '.join(str(text) for text in row.css(selector).getall()).strip()
                if selector.endswith('::attr(href)') and value:
                    value = safe_url(urljoin(session.page.url, value))
                item[key] = redact(value)[:2000]
            if any(item.values()):
                item['_source_url'] = safe_url(session.page.url)
                result.append(item)
        if not result:
            raise ValueError('没有提取到记录，请根据页面结构调整选择器；已有结果保留')
        unique_by = json.loads(args.get('unique_by') or '[]') or None
        try:
            limit = int(args.get('limit') or '2000')
        except ValueError:
            raise ValueError('limit 必须为整数') from None
        return append_rows(thread_id, list(fields) + ['_source_url'], result, unique_by=unique_by, limit=limit)


def operation_error_hint(exc):
    """Expose actionable browser diagnostics without raw traces, URLs or input values."""
    detail = str(exc)
    if 'intercepts pointer events' in detail:
        classes = re.findall(r'class="([A-Za-z0-9_ -]{1,160})"[^>]*>[^\n]*intercepts pointer events', detail)
        selectors = ['.' + '.'.join(value.split()) for value in classes[-2:] if value.split()]
        return '点击被页面浮层遮挡' + ('，观察到遮挡元素 ' + '、'.join(selectors) if selectors else '') + '；请观察并处理可关闭的普通弹窗后重试。'
    if 'not visible' in detail:
        return '目标元素存在但不可见；请检查是否需要悬停、展开菜单或滚动，不要重复点击隐藏元素。'
    if 'not enabled' in detail:
        return '目标控件当前不可用；请检查页面条件并等待状态变化。'
    if 'has been closed' in detail or 'Target closed' in detail:
        return '页面或浏览器已经关闭；请重新打开目标页面。'
    return '请重新观察目标区域和加载状态再决定下一步。'


class Host:
    """Lazy, single browser worker; subprocess support independent of ASGI loop."""
    def __init__(self):
        self.guard = threading.Lock()
        self.loop = None
        self.thread = None
        self.service = None

    def start(self):
        with self.guard:
            if self.thread and self.thread.is_alive():
                return
            ready = threading.Event()
            def worker():
                self.loop = asyncio.ProactorEventLoop() if os.name == 'nt' else asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.service = BrowserService(channel=os.getenv('SPIDERFLY_BROWSER_CHANNEL') or None)
                async def cleanup():
                    while True:
                        await asyncio.sleep(60)
                        async with self.service.lock:
                            await self.service.expire()
                self.loop.create_task(cleanup())
                ready.set()
                self.loop.run_forever()
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self.loop.close()
            self.thread = threading.Thread(target=worker, name='spiderfly-browser', daemon=True)
            self.thread.start()
            ready.wait(5)

    async def call(self, thread_id, name, args):
        self.start()
        future = asyncio.run_coroutine_threadsafe(self.service.run(thread_id, name, args), self.loop)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('浏览器操作未完成（' + type(exc).__name__ + '）：' + operation_error_hint(exc)) from None

    async def shutdown(self):
        if self.loop and self.thread and self.thread.is_alive():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.service.shutdown(), self.loop))
            self.loop.call_soon_threadsafe(self.loop.stop)
            await asyncio.to_thread(self.thread.join, 10)


host = Host()
