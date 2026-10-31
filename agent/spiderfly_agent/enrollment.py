"""Existing local identity can request approval again without deleting user data."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import uuid

import requests

from . import PROTOCOL_VERSION, __version__
from .api import Api, ApiError
from .identity import load_identity, public_key, save_json


def registration_status(directory: Path) -> dict:
    config_path = directory / 'config.json'
    if not config_path.exists():
        return {'state': 'unconfigured', 'message': '请填写接入码申请接入'}
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if not (directory / 'identity.json').is_file():
            raise ValueError('本机身份文件缺失，请填写接入码重新申请')
        _, key = load_identity(directory)
        api = Api(config['server'], key, config['host_id'])
        result = api.request('GET', '/api/agent/registration')
        if result.get('host_id') != config['host_id'] or result.get('approval_status') not in {'pending','approved','rejected','revoked'}:
            raise ValueError('主控登记响应无效，未确认接入成功')
        return {'state': result['approval_status'], 'host_id': result['host_id'], 'name': result['name'], 'server': api.server,
                'can_reapply': result.get('can_reapply', True)}
    except ApiError as exc:
        if exc.status == 404 and '宿主机不存在' in str(exc):
            return {'state': 'missing', 'message': '主控已删除原登记，请填写新接入码重新申请'}
        return {'state': 'error', 'message': str(exc)}
    except (requests.RequestException, OSError, ValueError, KeyError, TypeError) as exc:
        return {'state': 'error', 'message': str(exc)[:500]}


def apply(directory: Path, server: str, code: str, name: str) -> dict:
    config_path = directory / 'config.json'
    previous = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else None
    machine_id, key = load_identity(directory)
    api = Api(server, key)
    if previous and previous['server'].rstrip('/') != api.server:
        raise ValueError('已有身份不能直接切换主控，请使用新的数据目录申请')
    payload = {'enrollment_code': code, 'machine_id': machine_id, 'name': name,
               'public_key': public_key(key), 'agent_version': __version__, 'protocol_version': PROTOCOL_VERSION}
    message = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    payload['reenrollment_signature'] = base64.b64encode(key.sign(message)).decode('ascii')
    result = api.request('POST', '/api/agent/register', payload, signed=False)
    if result.get('protocol_version') != PROTOCOL_VERSION or type(result.get('host_id')) is not int or result['host_id'] < 1 or result.get('approval_status') not in {'pending','approved'}:
        raise ValueError('主控注册响应无效，未确认接入成功')
    if previous and previous['host_id'] != result['host_id']:
        # Old run IDs and pending events belong to the old registration. Keep
        # them locally rather than submitting them as the new host's results.
        paths = [directory / value for value in ('journal.sqlite3','journal.sqlite3-wal','journal.sqlite3-shm')]
        if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in paths):
            raise ValueError('原运行记录路径异常，未更改本机配置')
        if any(path.exists() for path in paths):
            history = directory / 'registration-history'
            if history.is_symlink() or (history.exists() and not history.is_dir()):
                raise ValueError('接入历史目录异常，未更改本机配置')
            archive = history / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
            archive.mkdir(parents=True)
            shutil.copy2(config_path, archive / 'config.json')
            for path in paths:
                if path.exists():
                    path.replace(archive / path.name)
    save_json(config_path, {'server': api.server, 'host_id': result['host_id'], 'name': result.get('name', name)})
    return result
