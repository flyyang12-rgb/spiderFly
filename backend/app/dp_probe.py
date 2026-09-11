"""Bounded DP page observation on the same serial queue as task scripts."""
import asyncio
import json
import time
from urllib.parse import urlsplit

from . import dp_runtime
from .database import transaction, fetch_one, utc_now


def init_tables(conn):
    conn.executescript('''CREATE TABLE IF NOT EXISTS ai_dp_probes (
        id INTEGER PRIMARY KEY, turn_id INTEGER NOT NULL REFERENCES ai_turns(id) ON DELETE CASCADE,
        url TEXT NOT NULL, selector TEXT NOT NULL, status TEXT NOT NULL,
        deadline REAL NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0,
        report TEXT NOT NULL DEFAULT '{}');''')
    conn.execute("UPDATE ai_dp_probes SET status='interrupted',report=? WHERE status IN ('pending','running')",
                 (json.dumps({'message': '服务重启，DP 页面检查已中断'}),))


async def request(url, selector, turn_id, hosts):
    from .ai_agent import _ensure_active
    from .maintenance_runtime import validate_contract
    validate_contract({'file': 'probe.json', 'required': ['text'], 'urls': [url]})
    if (urlsplit(url).hostname or '').lower().rstrip('.') not in hosts:
        raise ValueError('DP 检查网址必须来自当前用户提供或已发现的站点')
    if not selector or len(selector) > 500:
        raise ValueError('selector 使用 DP 定位语法；未知时填写 css:body')
    _ensure_active(turn_id)
    with transaction() as conn:
        probe_id = conn.execute("INSERT INTO ai_dp_probes(turn_id,url,selector,status,deadline) VALUES(?,?,?,'pending',?)",
            (turn_id, url, selector, time.time() + 120)).lastrowid
    try:
        while True:
            _ensure_active(turn_id)
            row = fetch_one('SELECT * FROM ai_dp_probes WHERE id=?', (probe_id,))
            if not row:
                raise InterruptedError('页面检查已删除')
            if row['status'] not in {'pending', 'running'}:
                return {'status': row['status'], **json.loads(row['report'])}
            if time.time() >= row['deadline']:
                raise TimeoutError('DP 页面检查超过时间上限')
            await asyncio.sleep(.25)
    finally:
        with transaction() as conn:
            conn.execute("UPDATE ai_dp_probes SET stop_requested=1,status=CASE WHEN status='pending' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('pending','running')", (probe_id,))


async def run_next():
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_dp_probes'").fetchone():
            return False
        row = conn.execute("SELECT * FROM ai_dp_probes WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if not row: return False
        job = dict(row)
        conn.execute("UPDATE ai_dp_probes SET status='running' WHERE id=?", (job['id'],))
    stop = asyncio.Event()
    async def watch():
        while not stop.is_set():
            row = fetch_one('SELECT p.stop_requested,t.stop_requested AS turn_stop,t.status FROM ai_dp_probes p JOIN ai_turns t ON t.id=p.turn_id WHERE p.id=?', (job['id'],))
            if not row or row['stop_requested'] or row['turn_stop'] or row['status'] != 'running' or time.time() >= job['deadline']:
                stop.set()
                return
            await asyncio.sleep(.25)
    watcher = asyncio.create_task(watch())
    report, status = {}, 'failed'
    try:
        remaining = int(job['deadline'] - time.time())
        if remaining < 1 or job['stop_requested']:
            raise InterruptedError('DP 页面检查已停止')
        source = '''import os, json
from pathlib import Path
from DrissionPage import Chromium, ChromiumOptions
co = ChromiumOptions(read_file=False).set_address(os.environ['SPIDERFLY_BROWSER_ADDRESS']).existing_only().headless()
page = Chromium(co).latest_tab
page.get(URL)
page.wait.doc_loaded(timeout=15)
page.wait(1)
nodes = page.eles(SELECTOR)
result = {'url': page.url, 'title': page.title, 'text': page.ele('css:body').text[:9000],
 'elements': [{'text': n.text[:1500], 'html': n.html[:2500]} for n in nodes[:8]],
 'links': [{'text': n.text[:200], 'href': n.attr('href'), 'class': n.attr('class')} for n in page.eles('css:a[href]')[:60]]}
(Path(os.environ['SPIDERFLY_ARTIFACT_DIR']) / 'probe.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
'''
        source = 'URL = ' + repr(job['url']) + '\nSELECTOR = ' + repr(job['selector']) + '\n' + source
        result = await dp_runtime.execute(source, hosts={urlsplit(job['url']).hostname.lower()}, timeout=remaining, stop=stop)
        if result['exit_code'] or not result.get('network', {}).get('requests'):
            raise ValueError(result['log'] or '未取得网页响应')
        report = {**json.loads(result['files']['probe.json']), 'browser_address': result['network']['browser_address']}
        status = 'success'
    except (InterruptedError, asyncio.CancelledError):
        status, report = 'cancelled', {'message': 'DP 页面检查已停止'}
        if asyncio.current_task().cancelling(): raise
    except Exception as exc:
        from .ai_settings import redact
        report = {'message': redact(str(exc))[:1500]}
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        with transaction() as conn:
            conn.execute('UPDATE ai_dp_probes SET status=?,report=? WHERE id=?', (status, json.dumps(report, ensure_ascii=False), job['id']))
    return True
