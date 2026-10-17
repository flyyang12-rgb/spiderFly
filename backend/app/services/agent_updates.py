"""Explicit administrator requests; immutable signed packages; host-owned results."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path
import secrets
import threading
import time
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from .. import database
from . import host_dispatch as hosts

REMOTE_VERSION = (0, 5, 0)
ACTIVE = {'queued', 'downloading', 'installing', 'awaiting_reconnect', 'uncertain'}
FINAL = {'succeeded', 'failed', 'expired', 'cancelled'}
MAX_PACKAGE = 16 * 1024 * 1024
_KEY_LOCK = threading.Lock()
TOP_FILES = ('README.md', 'requirements.txt', 'start.ps1', 'manage.ps1', 'setup.ps1',
             'install-python.ps1', '安装并接入Agent.bat', 'upgrade.ps1', 'upgrade-core.ps1',
             'upgrade-ui.ps1', 'upgrade-transport.py', '升级Agent.bat', 'remote-upgrade.ps1')
MODULES = ('__init__', '__main__', 'api', 'client', 'desktop', 'identity', 'journal',
           'windows', 'worker', 'environment', 'updater')


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def signing_key():
    # Reuse Windows DPAPI/atomic I/O; never persist a plaintext signing key.
    from ..ai_settings import _crypt, atomic_write
    path = hosts.DATA_DIR / 'agent-updates' / 'controller.key.dpapi'
    with _KEY_LOCK:
        try:
            if not path.exists():
                key = Ed25519PrivateKey.generate()
                raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
                atomic_write(path, _crypt(raw, decrypt=False))
            return Ed25519PrivateKey.from_private_bytes(_crypt(path.read_bytes(), decrypt=True))
        except (OSError, ValueError) as error:
            raise HTTPException(503, '主控升级签名不可用，请检查原 Windows 账号和数据目录') from error


def trust(key) -> dict:
    return {'format': 1, 'public_key': base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')}


def build_package(source: Path, version: str, key=None) -> bytes:
    names = list(TOP_FILES) + ['spiderfly_agent/' + name + '.py' for name in MODULES]
    entries = {}
    for name in names:
        path = source / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise HTTPException(503, 'Agent 安装文件不完整，请更新主控源码')
        content = path.read_bytes()
        if len(content) > 2 * 1024 * 1024:
            raise HTTPException(503, 'Agent 程序文件超过限制')
        entries[name] = content
    match = re.search(rb'(?m)^__version__\s*=\s*[\'"](\d+\.\d+\.\d+)[\'"]', entries['spiderfly_agent/__init__.py'])
    if not match or match.group(1).decode('ascii') != version or sum(map(len, entries.values())) > 8 * 1024 * 1024:
        raise HTTPException(503, 'Agent 源码版本与主控不一致，请完成更新后重试')
    entries['controller-trust.json'] = canonical(trust(key or signing_key()))
    manifest = {'format': 1, 'version': version,
                'files': {n: hashlib.sha256(b).hexdigest() for n, b in entries.items()}}
    entries['update-manifest.json'] = canonical(manifest)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, content in entries.items():
            # Stable timestamps make the same frozen source yield the same ZIP.
            item = zipfile.ZipInfo('SpiderFlyAgent/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(item, content)
    data = out.getvalue()
    if len(data) > MAX_PACKAGE:
        raise HTTPException(503, 'Agent 升级包超过限制')
    return data


def init_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS agent_update_jobs (
        id TEXT PRIMARY KEY, host_id INTEGER NOT NULL REFERENCES agent_hosts(id),
        request_id TEXT NOT NULL UNIQUE, from_version TEXT NOT NULL, target_version TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE, package_sha256 TEXT NOT NULL, package BLOB NOT NULL,
        offer_json TEXT NOT NULL, signature TEXT NOT NULL, status TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL DEFAULT '',
        requested_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
        updated_at REAL NOT NULL, expires_at REAL NOT NULL)''')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_update_active ON agent_update_jobs(host_id) WHERE status IN ('queued','downloading','installing','awaiting_reconnect','uncertain')")


def public(job) -> dict | None:
    if not job:
        return None
    return {key: job[key] for key in ('id', 'from_version', 'target_version', 'status',
                                      'message', 'created_at', 'updated_at')}


def latest(conn, host_id):
    return public(conn.execute('SELECT * FROM agent_update_jobs WHERE host_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (host_id,)).fetchone())


def release(conn, job):
    conn.execute("UPDATE agent_hosts SET remote_upgrade_id='',upgrade_token='',upgrade_expires_at=0 WHERE id=? AND remote_upgrade_id=? AND upgrade_token=?",
                 (job['host_id'], job['id'], job['token']))


def expire(conn):
    for row in conn.execute("SELECT * FROM agent_update_jobs WHERE expires_at<? AND status IN ('queued','downloading','installing','awaiting_reconnect')", (time.time(),)).fetchall():
        status = 'expired' if row['status'] == 'queued' else 'uncertain'
        message = '未领取，升级请求已过期' if status == 'expired' else '升级结果待确认，请检查目标电脑'
        conn.execute('UPDATE agent_update_jobs SET status=?,message=?,updated_at=? WHERE id=?', (status, message, time.time(), row['id']))
        if status == 'expired':
            release(conn, row)


def create(host_id: int, request_id: str, actor: dict, source: Path):
    with database.transaction() as conn:
        expire(conn)
        prior = conn.execute('SELECT * FROM agent_update_jobs WHERE request_id=?', (request_id,)).fetchone()
        if prior:
            if prior['host_id'] != host_id or prior['requested_by'] != actor['id']:
                raise HTTPException(409, '请求编号已用于其他升级')
            return public(prior)
        host = hosts.host_row(conn, host_id)
        if host['approval_status'] != 'approved':
            raise HTTPException(409, '仅已批准电脑可升级')
        if not hosts._online(host):
            raise HTTPException(409, '目标电脑离线，请连接后重试')
        if (hosts._version_tuple(host['agent_version']) or (0, 0, 0)) < REMOTE_VERSION or not host['remote_upgrade_ready']:
            raise HTTPException(409, '此 Agent 需先在本机更新到 0.5.0，之后支持主控升级')
        if host['active_run_id'] is not None or hosts._upgrading(host):
            raise HTTPException(409, '目标电脑忙碌或已有升级，请稍后重试')
        if (hosts._version_tuple(host['agent_version']) or (0, 0, 0)) >= hosts._version_tuple(hosts.CURRENT_AGENT_VERSION):
            raise HTTPException(409, '已是当前版本')
        key = signing_key()
        package = build_package(source, hosts.CURRENT_AGENT_VERSION, key)
        token = secrets.token_hex(16);job_id = secrets.token_hex(16);expires = time.time() + 1800
        descriptor = {'format': 1, 'id': job_id, 'host_id': host_id, 'machine_id': host['machine_id'],
                      'from_version': host['agent_version'], 'target_version': hosts.CURRENT_AGENT_VERSION,
                      'token': token, 'sha256': hashlib.sha256(package).hexdigest(), 'size': len(package),
                      'expires_at': expires}
        signed = base64.b64encode(key.sign(canonical(descriptor))).decode('ascii')
        conn.execute('''INSERT INTO agent_update_jobs
            (id,host_id,request_id,from_version,target_version,token,package_sha256,package,
             offer_json,signature,status,requested_by,created_at,updated_at,expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)''',
            (job_id, host_id, request_id, host['agent_version'], hosts.CURRENT_AGENT_VERSION, token,
             descriptor['sha256'], package, canonical(descriptor).decode('utf-8'), signed,
             actor['id'], database.utc_now(), time.time(), expires))
        conn.execute('UPDATE agent_hosts SET remote_upgrade_id=?,upgrade_token=?,upgrade_expires_at=? WHERE id=?',
                     (job_id, token, expires, host_id))
        hosts._audit(conn, actor, 'agent_upgrade', host_id, f"升级 Agent {host['agent_version']} → {hosts.CURRENT_AGENT_VERSION}")
        return public(conn.execute('SELECT * FROM agent_update_jobs WHERE id=?', (job_id,)).fetchone())


def offer(conn, host):
    if not host['remote_upgrade_id'] or host['approval_status'] != 'approved':
        return None
    row = conn.execute("SELECT * FROM agent_update_jobs WHERE id=? AND host_id=? AND status IN ('queued','downloading')", (host['remote_upgrade_id'], host['id'])).fetchone()
    if not row or row['expires_at'] <= time.time():
        return None
    return {'descriptor': json.loads(row['offer_json']), 'signature': row['signature']}


def owned(conn, host_id, job_id):
    host = hosts.host_row(conn, host_id)
    if host['approval_status'] != 'approved':
        raise HTTPException(403, '宿主机未获批准或已撤销')
    row = conn.execute('SELECT * FROM agent_update_jobs WHERE id=? AND host_id=?', (job_id, host_id)).fetchone()
    if not row:
        raise HTTPException(404, '升级请求不存在')
    return row


def download(host_id, job_id):
    with database.transaction() as conn:
        expire(conn)
        row = owned(conn, host_id, job_id)
        if row['status'] not in {'queued', 'downloading'} or row['expires_at'] <= time.time():
            raise HTTPException(409, '升级包已过期或请求状态已变化')
        conn.execute("UPDATE agent_update_jobs SET status='downloading',message='正在下载',updated_at=? WHERE id=?", (time.time(), job_id))
        return bytes(row['package'])


def report(host_id, job_id, payload):
    with database.transaction() as conn:
        row = owned(conn, host_id, job_id)
        if not secrets.compare_digest(row['token'], payload['token']):
            raise HTTPException(409, '升级操作不匹配')
        if payload['phase'] == 'installing':
            if payload['agent_version'] != row['from_version']:
                raise HTTPException(409, '原 Agent 版本已变化')
            if row['status'] == 'installing':
                return public(row)
            if row['status'] not in {'queued', 'downloading'} or row['expires_at'] <= time.time():
                raise HTTPException(409, '升级授权已失效')
            conn.execute("UPDATE agent_update_jobs SET status='installing',message='正在备份并升级',updated_at=? WHERE id=?", (time.time(), job_id))
        else:
            content = canonical(payload).decode('utf-8')
            if row['result_json']:
                if row['result_json'] != content:
                    raise HTTPException(409, '升级结果已记录，不能改写')
                return public(row)
            if row['status'] not in ACTIVE or payload['agent_version'] not in {row['from_version'], row['target_version']}:
                raise HTTPException(409, '升级状态或回传版本不匹配')
            if payload['success'] and (row['status'] not in {'installing', 'uncertain'} or payload['agent_version'] != row['target_version']):
                raise HTTPException(409, '尚无新版本安装事实')
            conn.execute("UPDATE agent_update_jobs SET status='awaiting_reconnect',result_json=?,message='等待版本与连接确认',updated_at=? WHERE id=?", (content, time.time(), job_id))
        return public(conn.execute('SELECT * FROM agent_update_jobs WHERE id=?', (job_id,)).fetchone())


def reconnected(conn, host, version):
    row = conn.execute("SELECT * FROM agent_update_jobs WHERE id=? AND host_id=? AND status IN ('awaiting_reconnect','uncertain')", (host['remote_upgrade_id'], host['id'])).fetchone()
    if not row or not row['result_json']:
        return
    result = json.loads(row['result_json'])
    if result['agent_version'] != version:
        return
    status = 'succeeded' if result['success'] else 'failed'
    message = f'已升级至 {version}' if result['success'] else result['message'] or '升级失败，原版本已重新连接'
    conn.execute('UPDATE agent_update_jobs SET status=?,message=?,updated_at=? WHERE id=?', (status, message[:1000], time.time(), row['id']))
    release(conn, row)
    hosts._audit(conn, None, 'agent_upgrade_' + status, host['id'], message[:1000])


def late_busy(conn, host, reported, recovery_required):
    if reported is None and not recovery_required:
        return
    row = conn.execute("SELECT * FROM agent_update_jobs WHERE id=? AND host_id=? AND status='queued'", (host['remote_upgrade_id'], host['id'])).fetchone()
    if row:
        conn.execute("UPDATE agent_update_jobs SET status='cancelled',message='检测到未结束运行，本次未升级',updated_at=? WHERE id=?", (time.time(), row['id']))
        release(conn, row)
