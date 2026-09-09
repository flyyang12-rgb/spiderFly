"""Installed only inside collection-v1; Scrapling selectors and routed Playwright."""
import base64
import json
import socket
from contextlib import contextmanager

from scrapling import Selector


def _exchange(message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(25)
        connection.connect('/broker/network.sock')
        connection.sendall(json.dumps(message).encode() + b'\n')
        with connection.makefile('rb') as stream:
            raw = stream.readline(9 * 1024 * 1024)
    result = json.loads(raw)
    if result.get('error'):
        raise PermissionError(result['error'])
    return result


def response(url, session=''):
    return _exchange({'url': url, 'session': session})


def get(url):
    """Live GET and Scrapling CSS/XPath selection. Supports same-site pagination."""
    reply = response(url)
    if reply['status'] != 200:
        raise ValueError(f"HTTP {reply['status']}: {url}")
    return Selector(content=base64.b64decode(reply['body']), url=reply['url'])


@contextmanager
def browser():
    """Temporary Chromium; all network goes through the trusted public GET broker."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as engine:
        instance = engine.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        try:
            context = instance.new_context(service_workers='block', accept_downloads=False)
            context.route_web_socket('**/*', lambda route: route.close())
            errors = []

            def route_request(route):
                request = route.request
                if request.method != 'GET' or request.resource_type in {'image', 'media', 'font'}:
                    route.abort()
                    return
                try:
                    reply = response(request.url)
                    route.fulfill(status=reply['status'], body=base64.b64decode(reply['body']),
                                  headers={'content-type': reply['content_type']})
                except Exception as exc:
                    errors.append(str(exc))
                    route.abort()

            context.route('**/*', route_request)
            yield context.new_page()
            if errors:
                raise ValueError('部分页面请求未完成：' + errors[0])
        finally:
            instance.close()


@contextmanager
def session(name='default'):
    """Isolated anonymous HTTP cookies for this execution only."""
    class Session:
        def get(self, url):
            reply = response(url, name)
            if reply['status'] != 200:
                raise ValueError(f"HTTP {reply['status']}: {url}")
            return Selector(content=base64.b64decode(reply['body']), url=reply['url'])
    yield Session()


@contextmanager
def dynamic_session():
    """Native Scrapling DynamicSession, routed before the first navigation."""
    from scrapling.fetchers import DynamicSession
    from pathlib import Path
    executable = next(Path('/browsers').glob('chromium_headless_shell-*/chrome-linux/headless_shell'), None)
    if executable is None:
        executable = next(Path('/browsers').glob('chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell'))
    import uuid
    session_id = 'dynamic-' + uuid.uuid4().hex
    errors = []
    def setup(page):
        page.route_web_socket('**/*', lambda route: route.close())
        def route_request(route):
            request = route.request
            if request.method != 'GET' or request.resource_type in {'image', 'media', 'font'}:
                route.abort()
                return
            try:
                reply = response(request.url, session_id)
                route.fulfill(status=reply['status'], body=base64.b64decode(reply['body']),
                              headers={'content-type': reply['content_type']})
            except Exception as exc:
                errors.append(str(exc))
                route.abort()
        page.route('**/*', route_request)
    with DynamicSession(headless=True, page_setup=setup, google_search=False, retries=1, timeout=15000,
                        executable_path=str(executable),
                        additional_args={'service_workers': 'block'},
                        extra_flags=['--disable-dev-shm-usage']) as native:
        class Session:
            def fetch(self, url, *, wait_for='body', page_action=None):
                def action(page):
                    try:
                        page.locator(wait_for).first.wait_for(timeout=15000)
                        if page_action: page_action(page)
                    except Exception as exc:
                        errors.append(str(exc))
                        raise
                result = native.fetch(url, page_action=action, load_dom=False)
                if errors:
                    raise ValueError('部分页面请求未完成：' + errors[0])
                return result
        yield Session()


def render(url, *, wait_for='body'):
    with dynamic_session() as current:
        return current.fetch(url, wait_for=wait_for)


def crawl(urls, parse, *, concurrency=2, max_pages=300, session_name=''):
    """parse(page) returns (rows, next_urls). Each parsed page is checkpointed.

    Fetches overlap; parse and checkpoint commits remain serial and deterministic.
    Completed checkpoints are cleared by the platform only after output validation.
    """
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urljoin
    import hashlib
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError('采集并发应为 1–4')
    if type(max_pages) is not int or not 1 <= max_pages <= 300:
        raise ValueError('max_pages 应为 1–300')
    urls = list(dict.fromkeys(urls))
    identity = hashlib.sha256(json.dumps([urls, max_pages, session_name]).encode()).hexdigest()
    saved = _exchange({'state': None})['state']
    if saved and saved.get('identity') != identity:
        raise ValueError('断点与当前采集参数不一致')
    state = saved or {'identity': identity, 'pending': urls, 'done': [], 'rows': []}
    if saved:
        print(f"继续采集：已完成 {len(state['done'])} 页，保留 {len(state['rows'])} 条")
    def fetch_page(url):
        reply = response(url, session_name)
        if reply['status'] != 200:
            raise ValueError(f"HTTP {reply['status']}: {url}")
        return Selector(content=base64.b64decode(reply['body']), url=reply['url'])
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while state['pending']:
            if len(state['done']) >= max_pages:
                raise ValueError('达到采集页数上限，断点已保存')
            batch = state['pending'][:min(concurrency, max_pages - len(state['done']))]
            futures = [pool.submit(fetch_page, url) for url in batch]
            for url, future in zip(batch, futures):
                page = future.result()
                rows, links = parse(page)
                rows, links = list(rows), list(links)
                if any(not isinstance(row, dict) for row in rows):
                    raise ValueError('parse 必须返回记录字典和后续网址')
                state['rows'].extend(rows)
                state['pending'].remove(url)
                state['done'].append(url)
                for link in links:
                    link = urljoin(page.url, link)
                    if link not in state['pending'] and link not in state['done']:
                        state['pending'].append(link)
                _exchange({'state': state})
                print(f"采集进度：{len(state['done'])} 页，{len(state['rows'])} 条")
    return state['rows']


def fetch(url, *, wait_for, mode='auto'):
    """Escalate only missing static content, not access denial or arbitrary errors."""
    if mode not in {'auto', 'http', 'dynamic'}:
        raise ValueError('mode 应为 auto/http/dynamic')
    if mode == 'dynamic':
        return render(url, wait_for=wait_for)
    page = get(url)
    if page.css(wait_for):
        return page
    if mode == 'http':
        raise ValueError('页面未找到要求的内容：' + wait_for)
    print('从 HTTP 切换为动态采集：静态页面未找到 ' + wait_for)
    return render(url, wait_for=wait_for)
