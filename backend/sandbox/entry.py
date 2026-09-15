"""Runs INSIDE bubblewrap. No host files, network, process spawning or credentials."""
import base64
import contextlib
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import resource
import sys
import traceback
import urllib.request
import urllib.response
from email.message import Message

sys.path.insert(0, '/deps')
import requests

request = json.loads(Path('/opt/task/input.json').read_text())
pages = request.get('pages', {})
collection = request.get('collection', False)
os.environ.update(SPIDERFLY_ARTIFACT_DIR='/work/artifacts', SPIDERFLY_RESULT_FILE='/work/result.json',
                  SPIDERFLY_WORK_DIR='/work', SPIDERFLY_EXECUTION_ID='0')
Path('/work/artifacts').mkdir()
if request.get('template'):
    # Input is on a read-only mount, never the uploaded original or shared workspace.
    os.environ['SPIDERFLY_TEMPLATE_FILE'] = '/opt/task/template.xlsx'

def page_for(url, method='GET', data=None):
    if method != 'GET' or data is not None or str(url) not in pages:
        raise PermissionError('只允许读取本次任务明确声明的 GET 网页快照：' + str(url)[:200])
    return pages[str(url)]

def urlopen(url, data=None, timeout=None, **kwargs):
    address = url.full_url if isinstance(url, urllib.request.Request) else str(url)
    method = url.get_method() if isinstance(url, urllib.request.Request) else 'GET'
    payload = url.data if isinstance(url, urllib.request.Request) else data
    page = page_for(address, method, payload)
    headers = Message()
    headers['Content-Type'] = page['content_type']
    reply = urllib.response.addinfourl(io.BytesIO(base64.b64decode(page['body'])), headers, address, page['status'])
    reply.status_code = page['status']
    return reply

def session_request(self, method, url, **kwargs):
    if any(kwargs.get(key) for key in ('params', 'data', 'json', 'files')):
        raise PermissionError('当前快照运行不接受额外请求参数或写入请求')
    page = page_for(str(url), method.upper())
    reply = requests.Response()
    reply.status_code = page['status']
    reply.url = str(url)
    reply.headers['Content-Type'] = page['content_type']
    reply._content = base64.b64decode(page['body'])
    reply.encoding = 'utf-8'
    return reply

if collection:
    sys.path.insert(0, '/opt/task')
    import spiderfly_collection
    def live_request(self, method, url, **kwargs):
        if method.upper() != 'GET' or any(kwargs.get(key) for key in ('data', 'json', 'files', 'cookies', 'auth')):
            raise PermissionError('采集环境只支持无凭据 GET')
        if kwargs.get('params'):
            url = requests.Request('GET', url, params=kwargs['params']).prepare().url
        page = spiderfly_collection.response(url)
        reply = requests.Response()
        reply.status_code, reply.url = page['status'], page['url']
        reply.headers['Content-Type'] = page['content_type']
        reply._content = base64.b64decode(page['body'])
        reply.encoding = 'utf-8'
        return reply
    requests.sessions.Session.request = live_request
else:
    urllib.request.urlopen = urlopen
    requests.sessions.Session.request = session_request

# Kernel-enforced syscall restrictions are installed before untrusted Python starts.
# Network is additionally absent at the namespace level; no host sockets are mounted.
seccomp = ctypes.CDLL('libseccomp.so.2', use_errno=True)
seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
seccomp.seccomp_init.restype = ctypes.c_void_p
seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
ctx = seccomp.seccomp_init(0x7fff0000)
if not ctx:
    raise RuntimeError('Cannot initialize seccomp')
for name in ('execve', 'execveat', 'fork', 'vfork', 'clone', 'clone3', 'ptrace', 'process_vm_writev',
             'mount', 'umount2', 'unshare', 'setns', 'bpf', 'keyctl', 'add_key', 'request_key', 'reboot'):
    if collection and name in {'execve', 'execveat', 'fork', 'vfork', 'clone', 'clone3'}:
        continue
    syscall = seccomp.seccomp_syscall_resolve_name(name.encode())
    if syscall >= 0 and seccomp.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, syscall, 0) != 0:
        raise RuntimeError('Cannot restrict syscall')
if seccomp.seccomp_load(ctx) != 0:
    raise RuntimeError('Cannot load seccomp')
seccomp.seccomp_release(ctx)
for kind, limit in ((resource.RLIMIT_AS, 512 * 1024 * 1024), (resource.RLIMIT_FSIZE, 8 * 1024 * 1024),
                    (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_CPU, min(300, request['timeout']))):
    if collection and kind == resource.RLIMIT_AS:
        continue  # Chromium reserves a large virtual address range. tmpfs/output/time remain bounded.
    if collection and kind == resource.RLIMIT_NOFILE:
        limit = 512
    resource.setrlimit(kind, (limit, limit))

if collection:
    resource.setrlimit(resource.RLIMIT_DATA, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

class BoundedLog(io.StringIO):
    def write(self, value):
        accepted = str(value)[:max(0, 64000 - self.tell())]
        super().write(accepted)
        sys.__stderr__.write(accepted)
        sys.__stderr__.flush()
        return len(value)

logs = BoundedLog()
status = 0
with contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
    try:
        code = compile(Path('/opt/task/main.py').read_text(), '/opt/task/main.py', 'exec')
        exec(code, {'__name__': '__main__', '__file__': '/opt/task/main.py'})
    except BaseException as error:
        if not isinstance(error, SystemExit) or error.code not in (None, 0):
            status = 1
            traceback.print_exc()

artifacts = []
total = 0
for path in sorted(Path('/work/artifacts').iterdir()):
    if path.is_symlink() or not path.is_file() or len(artifacts) >= 20:
        continue
    data = path.read_bytes()
    total += len(data)
    if total > 8 * 1024 * 1024:
        raise RuntimeError('产物超过 8MB 限额')
    artifacts.append({'name': path.name, 'data': base64.b64encode(data).decode()})
result_path = Path('/work/result.json')
result = result_path.read_text()[:65536] if result_path.is_file() and not result_path.is_symlink() else ''
print(json.dumps({'exit_code': status, 'log': logs.getvalue(), 'artifacts': artifacts, 'result': result}, ensure_ascii=True))
