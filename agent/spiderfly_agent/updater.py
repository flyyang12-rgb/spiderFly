"""Authenticated controller offers; download without blocking heartbeat; reuse PS updater."""
import base64
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__
from .api import Api, ApiError
from .identity import save_json


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,8}\.\d{1,8}\.\d{1,8}', value):
        raise ValueError('升级版本无效')
    return tuple(map(int, value.split('.')))


class RemoteUpdater:
    def __init__(self, api, root, machine_id, program=None):
        self.api, self.root, self.machine_id = api, Path(root).resolve(), machine_id
        self.program = Path(program or Path(__file__).resolve().parents[1]).resolve()
        self.marker = self.root / 'remote-update.json'
        self.thread = None
        self.lock = threading.RLock()
        self.ready = False
        self.error = ''
        try:
            pinned = self.root / 'controller-update-key.json'
            supplied = self.program / 'controller-trust.json'
            if not pinned.exists():
                value = json.loads(supplied.read_text('utf-8'))
                self.read_key(value)
                save_json(pinned, value)
            value = json.loads(pinned.read_text('utf-8'))
            self.key = self.read_key(value)
            if supplied.exists() and self.read_key(json.loads(supplied.read_text('utf-8'))).public_bytes_raw() != self.key.public_bytes_raw():
                raise ValueError('主控升级签名已变化，需在本机核对')
            self.ready = True
        except (OSError, ValueError, KeyError) as error:
            self.error = str(error)

    @staticmethod
    def read_key(value):
        if not isinstance(value, dict) or value.get('format') != 1:
            raise ValueError('主控升级签名格式无效')
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(value['public_key'], validate=True))

    def verify(self, offer, *, starting):
        if not self.ready or not isinstance(offer, dict) or set(offer) != {'descriptor', 'signature'}:
            raise ValueError('未配置可信主控升级签名')
        d = offer['descriptor']
        fields = {'format', 'id', 'host_id', 'machine_id', 'from_version', 'target_version', 'token', 'sha256', 'size', 'expires_at'}
        if not isinstance(d, dict) or set(d) != fields or d['format'] != 1 or not isinstance(offer['signature'], str):
            raise ValueError('升级指令格式无效')
        try:
            self.key.verify(base64.b64decode(offer['signature'], validate=True), canonical(d))
        except (InvalidSignature, ValueError) as error:
            raise ValueError('主控升级签名校验失败') from error
        if d['host_id'] != self.api.host_id or d['machine_id'] != self.machine_id:
            raise ValueError('升级指令不属于本机')
        if not all(isinstance(d[k], str) and re.fullmatch(pattern, d[k]) for k, pattern in
                   (('id', r'[a-f0-9]{32}'), ('token', r'[a-f0-9]{32}'), ('sha256', r'[a-f0-9]{64}'))):
            raise ValueError('升级指令标识无效')
        if type(d['size']) is not int or not 0 < d['size'] <= 16 * 1024 * 1024:
            raise ValueError('升级包大小无效')
        if not isinstance(d['expires_at'], (float, int)) or not math.isfinite(d['expires_at']):
            raise ValueError('升级授权时间无效')
        if version(d['target_version']) <= version(d['from_version']):
            raise ValueError('升级不能降级或重复安装同版本')
        if starting and (d['from_version'] != __version__ or d['expires_at'] <= time.time()):
            raise ValueError('升级授权已过期或原版本已变化')
        if not starting and __version__ not in {d['from_version'], d['target_version']}:
            raise ValueError('升级回传版本不匹配')
        return d

    def job_directory(self, job_id):
        base = (Path(os.environ['LOCALAPPDATA']) / 'SpiderFlyAgent-upgrades').resolve()
        path = base / ('remote-' + job_id)
        if not re.fullmatch(r'[a-f0-9]{32}', job_id) or not path.resolve().is_relative_to(base):
            raise ValueError('升级目录无效')
        return path

    def private_directory(self, path):
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('升级目录不能是链接')
        # Resolve the current SID without importing GUI or controller code.
        import ctypes
        from ctypes import wintypes
        token = wintypes.HANDLE();advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD();advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            data = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
            sid_text = wintypes.LPWSTR()
            advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                result = subprocess.run(['icacls.exe', str(path), '/inheritance:r', '/grant:r',
                                         '*' + sid_text.value + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                                        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                if result.returncode:
                    raise OSError('无法创建私有升级目录')
            finally:
                kernel.LocalFree.argtypes = [ctypes.c_void_p];kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        finally:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE];kernel.CloseHandle(token)

    def accept(self, offer):
        with self.lock:
            d = self.verify(offer, starting=True)
            if self.marker.exists():
                state = json.loads(self.marker.read_text('utf-8'))
                if state['offer']['descriptor']['id'] != d['id']:
                    raise ValueError('本机还有待确认的升级')
                return
            job = self.job_directory(d['id'])
            self.private_directory(job)
            save_json(self.marker, {'offer': offer, 'phase': 'accepted'})

    def observe(self, host):
        with self.lock:
            if not self.marker.exists():
                return
            state = json.loads(self.marker.read_text('utf-8'))
            job = host.get('agent_update') or {}
            if job.get('id') == state['offer']['descriptor']['id'] and job.get('status') in {'succeeded', 'failed', 'expired', 'cancelled'}:
                self.marker.unlink(missing_ok=True)

    def poll(self):
        with self.lock:
            if not self.ready or not self.marker.exists() or (self.thread and self.thread.is_alive()):
                return
            state = json.loads(self.marker.read_text('utf-8'))
            d = self.verify(state['offer'], starting=False)
            job = self.job_directory(d['id'])
            if state['phase'] == 'accepted' and d['expires_at'] > time.time():
                self.thread = threading.Thread(target=self.prepare, args=(state, job), daemon=True, name='agent-update-download')
                self.thread.start()
                return
            if state['phase'] == 'installing':
                progress = job / 'progress.json'
                if not progress.exists():
                    return  # Do not replay a possibly launched program replacement.
                result = json.loads(progress.read_text('utf-8-sig'))
                if not result.get('done'):
                    return
                state['phase'] = 'result'
                state['result'] = {'success': bool(result.get('success')) and __version__ == d['target_version'],
                                   'message': str(result.get('message', '升级未完成'))[:1000]}
                save_json(self.marker, state)
            if state['phase'] == 'accepted':
                state['phase'] = 'result';state['result'] = {'success': False, 'message': '升级授权已过期'}
                save_json(self.marker, state)
            if state['phase'] == 'result':
                payload = {'phase': 'result', 'token': d['token'], 'agent_version': __version__, **state['result']}
                try:
                    report = self.api.request('POST', f"/api/agent/updates/{d['id']}/report", payload)
                except ApiError as error:
                    if error.status != 409:
                        raise
                else:
                    if report['status'] in {'succeeded', 'failed', 'expired', 'cancelled'}:
                        self.marker.unlink(missing_ok=True)

    def prepare(self, state, job):
        api = Api(self.api.server, self.api.key, self.api.host_id)
        d = state['offer']['descriptor']
        try:
            self.verify(state['offer'], starting=True)
            archive = job / 'package.zip'
            api.download(f"/api/agent/updates/{d['id']}/package", archive, size=d['size'], sha256=d['sha256'])
            self.verify(state['offer'], starting=True)
            api.request('POST', f"/api/agent/updates/{d['id']}/report",
                        {'phase': 'installing', 'token': d['token'], 'agent_version': __version__, 'success': False, 'message': ''})
            with self.lock:
                state['phase'] = 'installing';save_json(self.marker, state)
            self.launch(job, d['token'])
        except (requests.RequestException, ApiError) as error:
            # Before replacement a transient network failure may safely retry.
            if isinstance(error, ApiError) and error.status < 500:
                self.failed(state, str(error))
            self.error = str(error)
        except (OSError, ValueError, RuntimeError) as error:
            self.failed(state, str(error))
        finally:
            api.session.close()

    def failed(self, state, message):
        with self.lock:
            state['phase'] = 'result';state['result'] = {'success': False, 'message': str(message)[:1000]}
            save_json(self.marker, state)

    def launch(self, job, token):
        # File logs avoid pipe inheritance preventing the old process from exiting.
        with (job / 'worker.out').open('ab') as out, (job / 'worker.err').open('ab') as err:
            subprocess.Popen(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                              '-File', str(self.program / 'remote-upgrade.ps1'), '-InstallDir', str(self.program),
                              '-DataDir', str(self.root), '-JobDir', str(job), '-Token', token],
                             cwd=self.program, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                             creationflags=subprocess.CREATE_NO_WINDOW)
