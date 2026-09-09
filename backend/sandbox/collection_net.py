"""Trusted GET-only broker. This module never executes task source."""
import base64
import ipaddress
import json
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit, urljoin

MAX_BODY = 2 * 1024 * 1024


def target(url, hosts):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if parsed.scheme not in {'http', 'https'} or host not in hosts or parsed.username or parsed.password or port not in {80, 443}:
        raise ValueError('只允许用户指定公开站点的 HTTP/HTTPS GET 请求')
    addresses = [item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('不可访问本机、内网或保留地址')
    return parsed, host, port, addresses[0]


class NativeSession:
    """Pinned Scrapling FetcherSession; its cookie jar never leaves this run."""
    def __init__(self):
        from scrapling.fetchers import FetcherSession
        self.manager = FetcherSession(impersonate=None, stealthy_headers=False,
                                      retries=1, follow_redirects=False, timeout=15)
        self.session = self.manager.__enter__()
        self.lock = threading.Lock()

    def close(self):
        self.manager.__exit__(None, None, None)

    def fetch(self, url, hosts):
        from curl_cffi import CurlOpt
        from curl_cffi.curl import CURL_WRITEFUNC_ERROR
        with self.lock:
            for _ in range(5):
                parsed, host, port, address = target(url, hosts)
                # Fixed curl options, not options supplied by task code. A fresh
                # connection prevents DNS cache/connection reuse crossing validation.
                address = '[' + address + ']' if ':' in address else address
                self.session._curl_session.curl_options = {
                    CurlOpt.RESOLVE: [f'{host}:{port}:{address}'.encode()],
                    CurlOpt.PROXY: b'', CurlOpt.FRESH_CONNECT: 1,
                    CurlOpt.FORBID_REUSE: 1,
                }
                chunks, size = [], 0
                def receive(chunk):
                    nonlocal size
                    size += len(chunk)
                    if size > MAX_BODY:
                        return CURL_WRITEFUNC_ERROR
                    chunks.append(chunk)
                    return len(chunk)
                try:
                    reply = self.session.get(url, content_callback=receive,
                        headers={'User-Agent': 'SpiderFly/0.3 public-collector'},
                        accept_encoding='identity')
                except Exception:
                    if size > MAX_BODY:
                        raise ValueError('单次响应超过 2MB') from None
                    raise
                if size > MAX_BODY:
                    raise ValueError('单次响应超过 2MB')
                if reply.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, reply.headers.get('location', ''))
                    continue
                if reply.status in {401, 403, 429}:
                    raise ValueError(f'访问受限（HTTP {reply.status}），请处理登录、验证码或访问频率')
                return {'url': url, 'status': reply.status,
                        'content_type': reply.headers.get('content-type', 'application/octet-stream'),
                        'body': base64.b64encode(b''.join(chunks)).decode()}
            raise ValueError('重定向次数过多')


def fetch(url, hosts):
    session = NativeSession()
    try:
        return session.fetch(url, hosts)
    finally:
        session.close()


class Broker(socketserver.ThreadingMixIn, getattr(socketserver, 'UnixStreamServer', socketserver.TCPServer)):
    daemon_threads = True

    def __init__(self, path, hosts, timeout, checkpoint=None):
        self.hosts = frozenset(hosts)
        self.deadline = time.monotonic() + timeout
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)
        self.sessions = {}
        self.next_request = 0.0
        self.checkpoint = checkpoint
        self.resumed = False
        self.count = 0
        self.total = 0
        self.trace = []
        self.failures = []
        super().__init__(str(path), Handler)

    def get(self, url, session=''):
        if not isinstance(session, str) or len(session) > 64:
            raise ValueError('会话标识无效')
        with self.slots:
            with self.lock:
                if self.count >= 300 or time.monotonic() >= self.deadline:
                    raise ValueError('达到采集请求或时间上限')
                self.count += 1
                delay = max(0, self.next_request - time.monotonic())
                self.next_request = time.monotonic() + delay + 0.1
                if session and session not in self.sessions:
                    if len(self.sessions) >= 8:
                        raise ValueError('最多 8 个采集会话')
                    self.sessions[session] = NativeSession()
                client = self.sessions.get(session)
            time.sleep(delay)
            page = client.fetch(url, self.hosts) if client else fetch(url, self.hosts)
            with self.lock:
                self.total += len(page['body'])
                if self.total > 32 * 1024 * 1024:
                    raise ValueError('采集响应总量超过限额')
                self.trace.append({'url': page['url'], 'status': page['status'], 'engine': 'Scrapling FetcherSession'})
            return page

    def state(self, value=None):
        from pathlib import Path
        with self.lock:
            if not self.checkpoint:
                if value is not None:
                    raise ValueError('本次运行未提供断点存储')
                return {'state': None}
            path = Path(self.checkpoint)
            if value is None:
                if not path.is_file():
                    return {'state': None}
                raw = path.read_bytes()
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('断点超过 8MB')
                saved = json.loads(raw)
                self.resumed = self.resumed or bool(saved.get('fetched'))
                return {'state': saved['state']}
            raw = json.dumps({'state': value, 'fetched': bool(self.trace) or self.resumed}, ensure_ascii=True).encode()
            if len(raw) > 8 * 1024 * 1024 or not isinstance(value, dict):
                raise ValueError('断点必须为 8MB 内的 JSON 对象')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            with temporary.open('wb') as stream:
                stream.write(raw)
                stream.flush()
                import os
                os.fsync(stream.fileno())
            temporary.replace(path)
            return {'saved': True}

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=20)
        # Driver is single-run; sessions are never shared with another task.
        for session in self.sessions.values():
            if session.lock.acquire(blocking=False):
                try: session.close()
                finally: session.lock.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(20)
        try:
            raw = self.rfile.readline(8 * 1024 * 1024 + 1025)
            if len(raw) > 8 * 1024 * 1024 + 1024:
                raise ValueError('请求过长')
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('请求格式无效')
            if set(message) == {'state'}:
                result = self.server.state(message['state'])
            elif set(message).issubset({'url', 'session'}) and isinstance(message.get('url'), str) and len(message['url']) <= 8000:
                result = self.server.get(message['url'], message.get('session', ''))
            else:
                raise ValueError('仅支持公开 GET、会话标识和断点')
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else '公开页面连接失败'
            self.server.failures.append(error[:300])
            result = {'error': error[:300]}
        try:
            self.wfile.write(json.dumps(result).encode() + b'\n')
        except OSError:
            pass
