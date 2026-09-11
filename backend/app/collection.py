"""AI collection trials run on the existing serial queue, with immutable inputs."""
import asyncio
import base64
import json
import hashlib
from pathlib import Path
import time
from urllib.parse import urlsplit

from . import maintenance_runtime as runtime, dp_runtime
from .database import transaction, fetch_one, utc_now

PROFILE = 'collection-v1'
MARKER = '# spiderfly-runtime: collection-v1'
PACKAGES = {**runtime.SUPPORTED, 'scrapling': '0.4.15', 'playwright': '1.62.0'}


def uses_collection(source):
    return MARKER in source.splitlines()[:10] or dp_runtime.uses_dp(source)


def validate(source, requirements):
    import re
    from .maintenance import declared_contract
    if not uses_collection(source):
        raise ValueError('采集草稿需在开头声明 ' + MARKER)
    if dp_runtime.uses_dp(source) and MARKER in source.splitlines()[:10]:
        raise ValueError('一个脚本只能声明一种运行环境')
    contract = declared_contract(source)
    if not contract or contract.get('effects') != 'artifacts_only' or not contract['urls']:
        raise ValueError('先按需求声明采集网址、结果文件、字段与行数，再试跑')
    packages = dp_runtime.PACKAGES if dp_runtime.uses_dp(source) else PACKAGES
    if dp_runtime.uses_dp(source):
        dp_runtime.validate_source(source)
    for line in requirements.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([\w-]+)(?:==([\d.]+))?', line)
        if not match or match[1].lower() not in packages or (match[2] and match[2] != packages[match[1].lower()]):
            raise ValueError('采集环境仅支持已准备的固定依赖：' + ', '.join(f'{k}=={v}' for k, v in packages.items()))
    return contract


def init_tables(conn):
    from . import dp_probe
    dp_probe.init_tables(conn)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS ai_collection_trials (
          id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL, turn_id INTEGER NOT NULL,
          status TEXT NOT NULL, hosts TEXT NOT NULL, deadline REAL NOT NULL,
          stop_requested INTEGER NOT NULL DEFAULT 0, report TEXT NOT NULL DEFAULT '{}',
          artifacts TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, ended_at TEXT,
          FOREIGN KEY(draft_id) REFERENCES ai_drafts(id) ON DELETE CASCADE,
          FOREIGN KEY(turn_id) REFERENCES ai_turns(id) ON DELETE CASCADE);
        CREATE UNIQUE INDEX IF NOT EXISTS ai_collection_active ON ai_collection_trials(draft_id)
          WHERE status IN ('pending','running');
    ''')
    conn.execute("UPDATE ai_collection_trials SET status='interrupted',report=?,ended_at=? WHERE status IN ('pending','running')",
                 (json.dumps({'message': '服务重启，试跑已中断，请重新试跑'}, ensure_ascii=False), utc_now()))


def view(row):
    if not row:
        return None
    return {'id': row['id'], 'status': row['status'], **json.loads(row['report'])}


async def request_trial(draft_id, thread, turn_id, hosts):
    from .ai_agent import _ensure_active
    draft = fetch_one('SELECT * FROM ai_drafts WHERE id=? AND thread_id=?', (draft_id, thread['id']))
    if not draft:
        raise ValueError('只能试跑当前对话的草稿')
    contract = validate(draft['source'], draft['requirements'])
    expected = {(urlsplit(url).hostname or '').lower().rstrip('.') for url in contract['urls']}
    if not expected.issubset(hosts):
        raise ValueError('草稿网址必须来自用户提供的站点，不能自行扩大采集范围')
    _ensure_active(turn_id)
    with transaction() as conn:
        cursor = conn.execute("INSERT INTO ai_collection_trials(draft_id,turn_id,status,hosts,deadline,created_at) VALUES(?,?,'pending',?,?,?)",
                              (draft_id, turn_id, json.dumps(sorted(expected)), time.time() + 120, utc_now()))
        trial_id = cursor.lastrowid
    try:
        while True:
            _ensure_active(turn_id)
            row = fetch_one('SELECT * FROM ai_collection_trials WHERE id=?', (trial_id,))
            if not row:
                raise InterruptedError('草稿已删除')
            if row['status'] not in {'pending', 'running'}:
                return view(row)
            if time.time() >= row['deadline']:
                raise TimeoutError('采集试跑达到时间上限')
            await asyncio.sleep(.25)
    finally:
        with transaction() as conn:
            conn.execute("UPDATE ai_collection_trials SET stop_requested=1,status=CASE WHEN status='pending' THEN 'cancelled' ELSE status END WHERE id=? AND status IN ('pending','running')", (trial_id,))


async def execute(source, requirements, contract, *, hosts=None, timeout=120, stop=None, state_scope=None):
    validate(source, requirements)
    contract = runtime.validate_contract(contract)
    if hosts is None:
        hosts = {(urlsplit(url).hostname or '').lower().rstrip('.') for url in contract['urls']}
    if dp_runtime.uses_dp(source):
        result = await dp_runtime.execute(source, hosts=hosts, timeout=timeout, stop=stop)
        if result['exit_code'] == 0 and not result.get('network', {}).get('requests'):
            result['exit_code'] = 1
            result['log'] += '\n没有实际网页请求，不能标记为采集验证通过。'
        return result
    checkpoint = None
    if state_scope:
        from .config import DATA_DIR
        digest = hashlib.sha256(json.dumps([source, requirements, contract, sorted(hosts)], sort_keys=True).encode()).hexdigest()
        scope = hashlib.sha256(str(state_scope).encode()).hexdigest()
        checkpoint = Path(DATA_DIR) / 'collection_state' / scope / (digest + '.json')
        # The queue serializes execution. Keep at most one version per task/draft.
        if checkpoint.parent.is_dir():
            for obsolete in checkpoint.parent.glob('*.json'):
                if obsolete != checkpoint and obsolete.is_file() and not obsolete.is_symlink():
                    obsolete.unlink()
    result = await runtime.execute_source(source, pages={}, timeout=timeout, stop=stop,
                                          profile=PROFILE, allowed_hosts=sorted(hosts), **({"checkpoint": checkpoint} if checkpoint else {}))
    errors = result.get('network', {}).get('errors', [])
    if any('访问受限' in message for message in errors):
        result['exit_code'] = 1
        result['log'] += '\n采集访问受限，请处理登录、验证码或频率限制。'
    if result['exit_code'] == 0 and not (result.get('network', {}).get('requests') or result.get('network', {}).get('resumed')):
        result['exit_code'] = 1
        result['log'] += '\n没有实际网页请求，不能标记为采集验证通过。'
    if checkpoint and result['exit_code'] == 0 and not (stop and stop.is_set()):
        try:
            runtime.validate_result(result, contract)
        except ValueError:
            pass  # Preserve the checkpoint until the original output contract passes.
        else:
            checkpoint.unlink(missing_ok=True)
    return result


async def run_next_trial():
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_collection_trials'").fetchone():
            return False
        row = conn.execute("SELECT c.*,d.source,d.requirements FROM ai_collection_trials c JOIN ai_drafts d ON d.id=c.draft_id WHERE c.status='pending' ORDER BY c.id LIMIT 1").fetchone()
        if not row:
            return False
        job = dict(row)
        conn.execute("UPDATE ai_collection_trials SET status='running' WHERE id=?", (job['id'],))
    stop = asyncio.Event()

    async def watch():
        while not stop.is_set():
            row = fetch_one('SELECT c.stop_requested,t.stop_requested AS turn_stop,t.status FROM ai_collection_trials c JOIN ai_turns t ON t.id=c.turn_id WHERE c.id=?', (job['id'],))
            if not row or row['stop_requested'] or row['turn_stop'] or row['status'] != 'running' or time.time() >= job['deadline']:
                stop.set()
                return
            await asyncio.sleep(.25)

    watcher = asyncio.create_task(watch())
    status, report, artifacts = 'failed', {}, {}
    try:
        remaining = int(job['deadline'] - time.time())
        if remaining < 1 or job['stop_requested']:
            raise InterruptedError('试跑已取消或达到时间上限')
        contract = validate(job['source'], job['requirements'])
        result = await execute(job['source'], job['requirements'], contract, hosts=json.loads(job['hosts']), timeout=min(120, remaining), stop=stop, state_scope=f"draft:{job['draft_id']}")
        artifacts = {name: base64.b64encode(value).decode() for name, value in result['files'].items()}
        report = {'log': result['log'], 'files': list(artifacts), 'network': result.get('network', {})}
        check = runtime.validate_result(result, contract)
        if stop.is_set():
            raise InterruptedError('试跑已停止')
        if not (result.get('network', {}).get('requests') or result.get('network', {}).get('resumed')):
            raise ValueError('没有实际网页请求，不能标记为采集验证通过')
        report.update(check)
        status = 'success'
    except (InterruptedError, asyncio.CancelledError):
        status, report = 'cancelled', {**report, 'message': '采集试跑已停止'}
        if asyncio.current_task().cancelling():
            raise
    except Exception as exc:
        from .ai_settings import redact
        report['message'] = redact(str(exc))[:1500]
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        with transaction() as conn:
            conn.execute('UPDATE ai_collection_trials SET status=?,report=?,artifacts=?,ended_at=? WHERE id=?',
                         (status, json.dumps(report, ensure_ascii=False), json.dumps(artifacts), utc_now(), job['id']))
    return True
