"""Trusted WSL bridge: bounded stdin/stdout, disposable tmpfs, fail-closed setup."""
from pathlib import Path
import base64
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time

runtime = Path.home() / '.local/share/spiderfly-sandbox'
bwrap = runtime / 'root/usr/bin/bwrap'
if len(sys.argv) == 2 and sys.argv[1] == '--check':
    assert bwrap.is_file() and (runtime / 'python/openpyxl').is_dir(), '请先安装受限运行环境'
    print((runtime / 'manifest.json').read_text())
    sys.exit(0)

data = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
if len(data) > 32 * 1024 * 1024:
    raise RuntimeError('Request too large')
request = json.loads(data)
timeout = max(1, min(300, int(request['timeout'])))
with tempfile.TemporaryDirectory(prefix='spiderfly-job-') as directory:
    work = Path(directory)
    (work / 'input.json').write_text(json.dumps(request))
    (work / 'main.py').write_text(request['source'])
    (work / 'entry.py').write_bytes(Path(__file__).with_name('entry.py').read_bytes())
    if request.get('template'):
        (work / 'template.xlsx').write_bytes(base64.b64decode(request['template'], validate=True))
    args = [str(bwrap), '--unshare-all', '--die-with-parent', '--new-session', '--clearenv', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--dir', '/etc', '--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache', '--proc', '/proc', '--dev', '/dev',
            '--size', '67108864', '--tmpfs', '/work', '--size', '16777216', '--tmpfs', '/tmp',
            '--ro-bind', str(work), '/opt/task', '--ro-bind', str(runtime / 'python'), '/deps',
            '--chdir', '/work', '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PATH', '/usr/bin',
            '/usr/bin/python3', '-I', '/opt/task/entry.py']
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in output:
        selector.register(stream, selectors.EVENT_READ)
    started = time.monotonic()
    try:
        while selector.get_map():
            if time.monotonic() - started > timeout + 5:
                raise TimeoutError('受限试跑超过时间预算')
            for item, _ in selector.select(0.1):
                chunk = os.read(item.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(item.fileobj)
                    continue
                output[item.fileobj].extend(chunk)
                if sum(map(len, output.values())) > 12 * 1024 * 1024:
                    raise RuntimeError('受限试跑输出超过限额')
        process.wait(timeout=2)
        if process.returncode != 0:
            raise RuntimeError('受限进程失败：' + output[process.stderr].decode(errors='replace')[-3000:])
        parsed = json.loads(output[process.stdout])
        if not isinstance(parsed, dict):
            raise ValueError('试跑回执无效')
        print(json.dumps(parsed, ensure_ascii=True))
    except BaseException as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        print(json.dumps({'exit_code': 1, 'log': str(error), 'artifacts': [], 'result': ''}, ensure_ascii=True))
    finally:
        selector.close()
