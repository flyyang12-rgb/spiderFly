"""Trusted browser lifecycle around a native Python task; not a security sandbox."""
import base64
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from urllib.parse import urlsplit


def browser_path():
    for base in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        root = os.environ.get(base)
        if root:
            for suffix in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
                path = Path(root) / suffix
                if path.is_file():
                    return str(path)
    raise ValueError('未找到 Edge 或 Chrome 浏览器')


class Log(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 64000:
            raise ValueError('DP 脚本日志超过限额')
        return super().write(value)


def main():
    from DrissionPage import Chromium, ChromiumOptions
    from websocket import create_connection
    work = Path(sys.argv[1]).resolve()
    hosts = set(json.loads(sys.argv[2]))
    port = int(os.environ['SPIDERFLY_BROWSER_PORT'])
    address = os.environ['SPIDERFLY_BROWSER_ADDRESS']
    profile = Path(os.environ['SPIDERFLY_BROWSER_PROFILE_DIR']).resolve()
    artifacts = work / 'artifacts'
    artifacts.mkdir()
    # Recheck immediately before launch. Never attach to an existing listener.
    with socket.socket() as guard:
        guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        guard.bind(('127.0.0.1', port))
    process = subprocess.Popen([browser_path(), f'--remote-debugging-port={port}',
        '--remote-debugging-address=127.0.0.1', f'--user-data-dir={profile}',
        '--enable-automation', '--headless=new', '--no-first-run', '--no-default-browser-check', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    log, packets, files = Log(), [], []
    code, result, browser = 1, '', None
    try:
        deadline = time.monotonic() + 15
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            if process.poll() is not None:
                raise ValueError('本次浏览器启动失败，未接管其他浏览器')
            try:
                with opener.open(f'http://{address}/json/version', timeout=1) as response:
                    endpoint = json.load(response)['webSocketDebuggerUrl']
                break
            except (OSError, ValueError, KeyError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('DP 浏览器启动超时')
            time.sleep(.1)
        if urlsplit(endpoint).hostname != '127.0.0.1' or urlsplit(endpoint).port != port:
            raise ValueError('浏览器调试地址不匹配')
        channel = create_connection(endpoint, timeout=5, suppress_origin=True)
        try:
            channel.send(json.dumps({'id': 1, 'method': 'Browser.getBrowserCommandLine'}))
            reply = json.loads(channel.recv())
            args = reply.get('result', {}).get('arguments', [])
            if f'--user-data-dir={profile}' not in args:
                raise ValueError('浏览器归属不匹配，未执行脚本，未关闭该浏览器')
            # Edge can start with background extensions but no page target.
            channel.send(json.dumps({'id': 2, 'method': 'Target.createTarget', 'params': {'url': 'about:blank'}}))
            reply = json.loads(channel.recv())
            if 'targetId' not in reply.get('result', {}):
                raise ValueError('无法创建本次浏览器页面')
        finally:
            channel.close()
        browser = Chromium(ChromiumOptions(read_file=False).set_address(address).existing_only().headless())
        tab = browser.latest_tab
        tab.listen.start()
        log.write(f'DP 4.1.1.4；浏览器 {address}；本次 PID {process.pid}\n')
        with redirect_stdout(log), redirect_stderr(log):
            try:
                runpy.run_path(str(work / 'main.py'), run_name='__main__')
                code = 0
            except BaseException:
                traceback.print_exc()
        for packet in tab.listen.steps(timeout=.2):
            if len(packets) >= 200:
                break
            url = packet.url
            if (urlsplit(url).hostname or '').lower().rstrip('.') in hosts:
                packets.append({'url': url, 'status': packet.response.status})
        for path in artifacts.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError('DP 产物只允许普通文件')
            if len(files) >= 20 or path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('DP 产物超过限额')
            files.append({'name': path.name, 'data': base64.b64encode(path.read_bytes()).decode()})
        receipt = work / 'result.json'
        if receipt.exists():
            if receipt.is_symlink() or receipt.stat().st_size > 65536:
                raise ValueError('DP 回执无效')
            result = receipt.read_text(encoding='utf-8')
    except BaseException as exc:
        code = 1
        log = io.StringIO(log.getvalue()[:60000] + '\n' + str(exc))
    finally:
        # Kill only the process tree we created, never by image name or port.
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            process.wait(timeout=5)
    print(json.dumps({'exit_code': code, 'log': log.getvalue(), 'artifacts': files, 'result': result,
        'network': {'requests': len(packets), 'pages': packets, 'browser_address': address,
                    'runtime': 'drissionpage-v1'}}, ensure_ascii=True))


if __name__ == '__main__':
    main()
