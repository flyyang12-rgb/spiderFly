"""Native Scrapling HTTP/headless probes. Fixed code, no generated host execution."""
from __future__ import annotations

import json
import re
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urljoin, parse_qsl, quote, urlencode, urlunsplit

from . import ai_tools
from .ai_settings import redact


def public(url):
    return ai_tools.public_target(url, {(urlsplit(url).hostname or '').lower().rstrip('.')})


def request(url, mode='auto', wait_for=''):
    from scrapling.fetchers import Fetcher, DynamicFetcher, StealthyFetcher
    import certifi
    public(url)
    if mode not in {'auto', 'http', 'dynamic', 'stealth'}:
        raise ValueError('mode 为 auto/http/dynamic/stealth')
    trace = []
    if mode in {'auto', 'http'}:
        # curl on Windows can fail to read a CA bundle in a non-ASCII venv path.
        # Copy the same trusted CA bytes; never disable certificate verification.
        with tempfile.TemporaryDirectory(prefix='spiderfly-ca-') as directory:
            ca = Path(directory) / 'ca.pem'
            ca.write_bytes(Path(certifi.where()).read_bytes())
            current = url
            for _ in range(5):
                public(current)
                page = Fetcher.get(current, impersonate='chrome', stealthy_headers=True,
                                   verify=str(ca), timeout=20, retries=1, follow_redirects=False)
                trace.append({'engine': 'Fetcher', 'status': page.status})
                if page.status in {301, 302, 303, 307, 308}:
                    current = urljoin(current, page.headers.get('location', ''))
                    continue
                break
            else:
                raise ValueError('重定向次数过多')
        if page.status in {401, 403, 429}:
            raise ValueError(f'原生 HTTP 返回 {page.status}；请暂停或降低频率，不自动重试')
        text = page.get_all_text(ignore_tags=('script', 'style', 'noscript'))
        missing = not page.css(wait_for) if wait_for else len(text.strip()) < 200
        if mode == 'http' or not missing:
            return snapshot(page, 'Fetcher', trace)
        trace.append({'reason': '静态响应缺少要求的内容，切换原生无头 StealthyFetcher'})
    fetcher = DynamicFetcher if mode == 'dynamic' else StealthyFetcher
    failures = []
    def setup(page):
        def route_request(route):
            try:
                public(route.request.url)
            except Exception:
                failures.append('非公开地址或公网校验失败')
                route.abort()
                return
            route.continue_()
        page.route('**/*', route_request)
        page.route_web_socket('**/*', lambda route: route.close())
    options = dict(headless=True, page_setup=setup, timeout=25000, retries=1,
                   wait=2000, network_idle=False, google_search=False)
    if wait_for:
        options['wait_selector'] = wait_for
    # Use the installed Chromium executable for either Playwright or Patchright.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as engine:
        options['executable_path'] = engine.chromium.executable_path
    if fetcher is StealthyFetcher:
        options['solve_cloudflare'] = False
    page = fetcher.fetch(url, **options)
    trace.append({'engine': fetcher.__name__, 'status': page.status, 'blocked_requests': len(failures)})
    return snapshot(page, fetcher.__name__, trace)


def snapshot(page, engine, trace):
    if len(page.html_content.encode()) > 4 * 1024 * 1024:
        raise ValueError('原生采集页面超过 4MB')
    return {'html': page.html_content, 'url': page.url, 'engine': engine,
            'status': page.status, 'trace': trace}


def inspect(snapshot, selector='body'):
    from scrapling import Selector
    from .ai_browser import safe_url
    page = Selector(snapshot['html'], url=snapshot['url'])
    # Menus can occupy the beginning of the DOM; expose repeated record regions
    # even when cards use click handlers rather than href attributes.
    groups = {}
    for node in page.css('li[class], article[class], tr[class]'):
        classes = [c for c in node.attrib.get('class', '').split() if re.fullmatch(r'[A-Za-z_][\w-]*', c)]
        text = node.get_all_text(ignore_tags=('script', 'style')).strip()
        if not classes or not node.css('a') or not 15 <= len(text) <= 1500:
            continue
        key = node.tag + '.' + classes[0]
        group = groups.setdefault(key, {'selector': key, 'count': 0, 'sample': text[:300]})
        group['count'] += 1
    regions = sorted((g for g in groups.values() if g['count'] > 1), key=lambda g: -g['count'])[:20]
    nodes = page.css(selector or 'body')
    if not nodes:
        raise ValueError('当前快照未找到该选择器；实际重复区域：' + json.dumps(regions, ensure_ascii=False))
    parser = ai_tools.PageText()
    html = '\n'.join(node.html_content for node in nodes[:3])
    html = re.sub(r'(?<=href=")[^"]+', lambda m: safe_url(urljoin(snapshot['url'], m.group())), html)
    parser.feed(html)
    links = []
    for link in page.css('a[href]'):
        href = urljoin(snapshot['url'], link.attrib.get('href', ''))
        if urlsplit(href).scheme not in {'http', 'https'}:
            continue
        links.append({'text': link.get_all_text()[:100], 'url': safe_url(href),
                      'class': link.attrib.get('class', ''),
                      'parents': [node.attrib.get('class', '') for node in link.xpath('ancestor::*')[-3:]]})
        if len(links) >= 80:
            break
    text = '\n'.join(parser.text)
    result = {'engine': snapshot['engine'], 'status': snapshot['status'],
              'url': safe_url(snapshot['url']), 'title': page.css('title::text').get(default=''),
              'text': text[:3500], 'structure': '\n'.join(parser.structure)[:6500],
              'links': links, 'regions': regions, 'trace': snapshot['trace'],
              'note': '真实页面快照；登录入口不代表数据一定不可读。需用选择器和实际记录判断。结构截断时可 scrape_inspect 指定区域。'}
    return json.loads(redact(json.dumps(result, ensure_ascii=False)))


class SnapshotPage:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.url = snapshot['url']

    async def content(self):
        return self.snapshot['html']


async def live_action(page, args):
    action, selector, value = args['action'], args['selector'], args['value']
    if action == 'wait':
        if not value.isdigit() or not 1 <= int(value) <= 10:
            raise ValueError('wait 的 value 为 1–10 秒')
        await page.wait_for_timeout(int(value) * 1000)
        return
    if action == 'scroll':
        if value not in {'up', 'down'}:
            raise ValueError('scroll 的 value 为 up/down')
        await page.mouse.wheel(0, 800 if value == 'down' else -800)
    elif action in {'click', 'hover', 'fill', 'select', 'press'}:
        if not selector or len(selector) > 500:
            raise ValueError('请使用实际观察到的唯一选择器，最长 500 字')
        target = page.locator(selector)
        if await target.count() != 1:
            raise ValueError('选择器未唯一定位元素，请先 scrape_inspect 缩小区域')
        if action == 'fill':
            if await target.get_attribute('type') == 'password' or len(value) > 1000:
                raise ValueError('不能自动填写密码；普通输入最多 1000 字')
            await target.fill(value)
        elif action == 'select':
            await target.select_option(label=value)
        elif action == 'press':
            if value not in {'Enter', 'Tab', 'Escape', 'ArrowDown', 'ArrowUp'}:
                raise ValueError('不支持的按键')
            await target.press(value)
        else:
            await getattr(target, action)()
    else:
        raise ValueError('action 为 click/hover/fill/select/press/scroll/wait')
    await page.wait_for_timeout(1000)


def json_evidence(data, post_data=''):
    """Return shapes/counts and query parameters; never return record payloads."""
    result = {'lists': []}
    def walk(value, path='', depth=0):
        if depth > 4:
            return
        if isinstance(value, list):
            fields = list(value[0])[:40] if value and isinstance(value[0], dict) else []
            result['lists'].append({'path': path, 'count': len(value), 'fields': fields})
        elif isinstance(value, dict):
            for key, child in list(value.items())[:50]:
                child_path = path + '.' + key if path else key
                if key in {'code', 'message', 'hasMore', 'totalCount', 'resCount'} and isinstance(child, (str, int, bool)):
                    result[child_path] = child[:200] if isinstance(child, str) else child
                if isinstance(child, (list, dict)):
                    walk(child, child_path, depth + 1)
    walk(data)
    result['lists'] = result['lists'][:12]
    try:
        params = json.loads(post_data) if post_data.startswith('{') else dict(parse_qsl(post_data))
    except (ValueError, TypeError):
        params = {}
    if isinstance(params, dict):
        result['parameters'] = {key: value for key, value in params.items()
            if key.lower() in {'page', 'pagesize', 'offset', 'limit', 'city', 'query', 'keyword', 'scene', 'multibusinessdistrict'}
            and isinstance(value, (str, int, bool)) and len(str(value)) <= 300}
    return result


async def response_evidence(reply, capture=None):
    try:
        if 'json' not in reply.headers.get('content-type', ''):
            return {}
        if int(reply.headers.get('content-length', '0')) > 2 * 1024 * 1024:
            return {}
        body = await reply.body()
        if len(body) > 2 * 1024 * 1024:
            return {}
        data = json.loads(body)
        result = json_evidence(data, (reply.request.post_data or '')[:8000])
        if capture and result['lists']:
            result['response_id'] = capture(data)
        return {'json': result}
    except Exception:
        return {}


def extract_json(data, args, *, base_url=''):
    def lookup(value, path):
        if not re.fullmatch(r'[\w]+(?:\.[\w]+)*', path):
            raise ValueError('JSON 路径只支持点分隔字段名')
        if re.search(r'password|token|cookie|security|session|authorization|secret|phone|email|gps|contact', path, re.I):
            raise ValueError('不提取凭据或私人联系方式字段')
        for name in path.split('.'):
            value = value.get(name) if isinstance(value, dict) else None
        return value
    fields, filters = json.loads(args['fields']), json.loads(args['filters'])
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 20 or '_source_url' in fields or any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()):
        raise ValueError('fields 为最多20个字段名到 JSON 路径的对象')
    if not isinstance(filters, dict) or any(key not in fields for key in filters):
        raise ValueError('filters 只能使用已选择的字段名')
    for rules in filters.values():
        if not isinstance(rules, dict) or set(rules) - {'equals', 'contains_any', 'excludes_any'}:
            raise ValueError('筛选规则支持 equals/contains_any/excludes_any')
        for op, expected in rules.items():
            if op == 'equals' and not isinstance(expected, str):
                raise ValueError('equals 需要字符串')
            if op != 'equals' and (not isinstance(expected, list) or not expected or any(not isinstance(v, str) or not v for v in expected)):
                raise ValueError('包含/排除条件需要非空字符串数组')
    rows = lookup(data, args['rows'])
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('rows 必须定位最多500项的真实 JSON 数组')
    result = []
    for record in rows:
        item = {}
        for key, path in fields.items():
            if '{' in path or path.startswith(('https://', 'http://')):
                is_url = path.startswith(('https://', 'http://'))
                if is_url:
                    parsed = urlsplit(path)
                    if not base_url or parsed.hostname != urlsplit(base_url).hostname or parsed.username or parsed.password or '{' in parsed.netloc:
                        raise ValueError('链接模板必须使用当前页面的同站地址')
                def replace(match):
                    scalar = lookup(record, match.group(1))
                    value = str(scalar) if isinstance(scalar, (str, int, float, bool)) else ''
                    return quote(value, safe='') if is_url else value
                value = re.sub(r'\{([\w.]+)\}', replace, path)
                if '{' in value or '}' in value:
                    raise ValueError('链接模板占位符必须是点分隔字段路径')
            else:
                value = lookup(record, path)
            item[key] = str(value)[:2000] if isinstance(value, (str, int, float, bool)) else ''
        valid = True
        for key, rules in filters.items():
            actual = item[key].casefold()
            for op, expected in rules.items():
                if op == 'equals' and actual != expected.casefold(): valid = False
                if op == 'contains_any' and not any(v.casefold() in actual for v in expected): valid = False
                if op == 'excludes_any' and any(v.casefold() in actual for v in expected): valid = False
        if valid:
            result.append(item)
    return list(fields), result


def prepare_replay(captured, changes):
    """Only paginate an observed read/list request; preserve all search conditions."""
    parsed = urlsplit(captured['request_url'])
    if (not re.search(r'/(?:search|list|query|catalog|feed|items|jobs|products|articles|recommend)(?:/|\.|$)', parsed.path, re.I)
        or re.search(r'send|apply|pay|order|save|delete|create|update|remove', parsed.path, re.I)):
        raise ValueError('只支持已观察到的只读搜索/列表接口翻页')
    method = captured.get('method')
    content_type = captured.get('content_type', '')
    body = captured.get('body', '')
    if len(body.encode()) > 8000 or method not in {'GET', 'POST'}:
        raise ValueError('请求不支持安全分页')
    is_json = method == 'POST' and 'json' in content_type
    def form_values(text):
        pairs = parse_qsl(text, keep_blank_values=True)
        if len(pairs) != len(dict(pairs)):
            raise ValueError('重复参数不能安全重放，请继续使用页面操作')
        return dict(pairs)
    if method == 'GET':
        params = form_values(parsed.query)
    elif is_json:
        params = json.loads(body)
    elif 'application/x-www-form-urlencoded' in content_type:
        params = form_values(body)
    else:
        raise ValueError('只支持 GET、JSON 或表单列表请求')
    if not isinstance(params, dict) or not isinstance(changes, dict) or not changes:
        raise ValueError('pagination 需要已观察分页字段的 JSON 对象')
    for key, value in changes.items():
        if key not in params or key.lower() not in {'page', 'pagesize', 'offset', 'limit'} or type(value) is not int or not 0 <= value <= 500:
            raise ValueError('只能调整已存在的 page/pageSize/offset/limit，值为0–500整数；城市与关键词等条件保持不变')
        params[key] = value if is_json else str(value)
    if method == 'GET':
        url = urlunsplit(parsed._replace(query=urlencode(params)))
    else:
        url = captured['request_url']
        body = json.dumps(params, ensure_ascii=False) if is_json else urlencode(params)
    return {'url': url, 'method': method, 'body': body, 'content_type': content_type}
