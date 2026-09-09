"""WSL-only transport integration against a synthetic HTTP fixture."""
import concurrent.futures
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import sys, threading, time, tempfile, json
from unittest.mock import patch
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sandbox'))
import collection_net as net

active = 0
peak = 0
active_lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location','http://private.invalid/'); self.end_headers(); return
        if self.path == '/slow':
            global active, peak
            with active_lock:
                active += 1; peak = max(peak, active)
            time.sleep(.3)
            with active_lock: active -= 1
        self.send_response(200)
        if self.path == '/set': self.send_header('Set-Cookie','visit=one; Path=/')
        self.end_headers()
        if self.path == '/large':
            try: self.wfile.write(b'x'*(net.MAX_BODY+1))
            except OSError: pass
        else: self.wfile.write(self.headers.get('Cookie','empty').encode())

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
port=server.server_address[1]
seen=[]
def fixture_target(url, hosts):
    parsed=urlsplit(url)
    if parsed.hostname != 'fixture.invalid': raise ValueError('redirect rejected')
    seen.append(url)
    return parsed,parsed.hostname,port,'127.0.0.1'
url=f'http://fixture.invalid:{port}'
try:
    with tempfile.TemporaryDirectory() as root, patch.object(net,'target',side_effect=fixture_target):
        broker=net.Broker(Path(root)/'net.sock',{'fixture.invalid'},30,str(Path(root)/'state.json')).start()
        try:
            broker.get(url+'/set','A')
            import base64
            assert base64.b64decode(broker.get(url+'/read','A')['body'])==b'visit=one'
            assert base64.b64decode(broker.get(url+'/read','B')['body'])==b'empty'
            try: broker.get(url+'/redirect')
            except ValueError as e: assert 'redirect rejected' in str(e)
            else: raise AssertionError('redirect escaped validation')
            try: broker.get(url+'/large')
            except ValueError as e: assert '2MB' in str(e)
            else: raise AssertionError('oversized response accepted')
            started=time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda _:broker.get(url+'/slow'),range(4)))
            assert 1 < peak <= 4, 'fetches did not overlap or exceeded limit'
            import collection_api as api
            with patch.object(api,'get') as http, patch.object(api,'render',return_value='dynamic') as dynamic:
                http.return_value.css.return_value=[]
                assert api.fetch(url,wait_for='.items')=='dynamic'
                dynamic.assert_called_once()
                dynamic.reset_mock()
                http.side_effect=PermissionError('blocked')
                try: api.fetch(url,wait_for='.items')
                except PermissionError: pass
                else: raise AssertionError('access error swallowed')
                dynamic.assert_not_called()
            fail_once = [True]
            def exchange(message):
                if 'state' in message: return broker.state(message['state'])
                return broker.get(message['url'],message.get('session',''))
            def parse(page):
                if page.url.endswith('/second') and fail_once[0]:
                    fail_once[0]=False
                    raise ValueError('synthetic parse failure')
                return ([{'url':page.url}], [url+'/second'] if page.url.endswith('/first') else [])
            with patch.object(api,'_exchange',side_effect=exchange):
                try: api.crawl([url+'/first'],parse)
                except ValueError as e: assert 'synthetic parse failure' in str(e)
                else: raise AssertionError('failure was swallowed')
                saved=broker.state()['state']
                assert saved['done']==[url+'/first'] and saved['pending']==[url+'/second']
                before=len([x for x in seen if x.endswith('/first')])
                rows=api.crawl([url+'/first'],parse)
                assert len(rows)==2
                assert before==len([x for x in seen if x.endswith('/first')])
            broker.state({'rows':[{'name':'a'}],'pending':['next']})
            assert broker.state()['state']['pending']==['next']
        finally: broker.close()
        second=net.Broker(Path(root)/'next.sock',{'fixture.invalid'},30,str(Path(root)/'state.json')).start()
        try:
            assert second.state()['state']['rows']==[{'name':'a'}]
            assert base64.b64decode(second.get(url+'/read','A')['body'])==b'empty'
        finally: second.close()
    print('native session, redirect, response cap, concurrency, restart checkpoint: passed')
finally:
    server.shutdown();server.server_close()
