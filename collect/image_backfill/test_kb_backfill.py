import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import kb_backfill as kb
from kb_rate import RequestGovernor
from kb_transport import CurlTransport


class Clock:
    def __init__(self): self.now = 1000.
    def time(self): return self.now
    def sleep(self, n): self.now += n


class GovernorTests(unittest.TestCase):
    def test_waiters_respect_429_and_restart(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, 'meta').mkdir()
            clock = Clock()
            with patch('kb_rate.time.monotonic', clock.time), patch('kb_rate.time.time', clock.time), patch('kb_rate.time.sleep', clock.sleep):
                g = RequestGovernor(td, .5, .08, .7)
                g.pace()
                g.response(429, 120)
                # The other lane receives a success after the 429: no early release.
                g.response(200)
                g.pace()
                self.assertGreaterEqual(clock.now, 1120)
                self.assertEqual(g.rps, .25)
                g.response(429, 300)
                restored = RequestGovernor(td, .6, .08, .7)
                restored.pace()
                self.assertGreaterEqual(clock.now, 1420)
                self.assertEqual(restored.rps, .125)

    def test_retry_after_http_date(self):
        from email.utils import formatdate
        with tempfile.NamedTemporaryFile(mode='w') as f:
            f.write('HTTP/1.1 429 Too Many Requests\r\nRetry-After: '+formatdate(1120, usegmt=True)+'\r\n');f.flush()
            with patch.object(kb._fc.time, 'time', return_value=1000):
                self.assertEqual(kb._fc.parse_retry_after(f.name), 120)


class TransportTests(unittest.TestCase):
    def test_real_http_reuse_throttle_and_size_cap(self):
        try:
            import pycurl
        except ImportError:
            self.skipTest('pycurl verified on fleet host')
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self,*a):pass
            def do_GET(self):
                data=b'x'*(4096 if self.path=='/big' else 100)
                self.send_response(429 if self.path=='/throttle' else 200)
                self.send_header('Content-Length',str(len(data)))
                if self.path=='/throttle':self.send_header('Retry-After','120')
                self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                t=CurlTransport('', ['-A','test/1.0'],5,1024,'pycurl')
                url=f'http://127.0.0.1:{server.server_port}'
                p,h=str(Path(td,'body')),str(Path(td,'headers'))
                try:
                    a=t.fetch(url+'/',p,h); b=t.fetch(url+'/',p,h)
                    self.assertEqual((a['num_connects'],b['num_connects']),(1,0))
                    self.assertEqual(Path(p).stat().st_size,100)
                    r=t.fetch(url+'/throttle',p,h)
                    self.assertEqual(r['http_code'],429)
                    self.assertEqual(kb._fc.parse_retry_after(h),120)
                    r=t.fetch(url+'/big',p,h)
                    self.assertNotEqual(r['curl_code'],0)
                    self.assertLessEqual(Path(p).stat().st_size,1024)
                finally:t.close()
        finally:server.shutdown();server.server_close();thread.join()

    def test_cos_network_exception_is_retryable(self):
        try:
            import requests
        except ImportError:
            import urllib.error
            with patch.object(kb.cos_util,"creds",return_value=("test","test")), patch("urllib.request.urlopen",side_effect=urllib.error.URLError("offline")):
                self.assertEqual(kb.cos_util._call("PUT","test",b"test"),(0,{},b"transport_error"))
            return
        with patch.object(kb.cos_util,'creds',return_value=('test','test')), patch.object(requests.Session,'request',side_effect=requests.ConnectionError('offline')):
            self.assertEqual(kb.cos_util._call('PUT','test',b'test'),(0,{},b'transport_error'))


class PipelineTests(unittest.TestCase):
    def run_pipeline(self, td, transport):
        rows=[{'qid':str(i), 'commons_file':f'{i}.jpg', 'ext':'jpg'} for i in range(100)]
        tasks=Path(td,'tasks.jsonl'); tasks.write_text(''.join(json.dumps(r)+'\n' for r in rows+rows))
        mf=Path(td,'out','manifest.jsonl')
        argv=['kb_backfill.py','--tasks',str(tasks),'--shard','0/1','--manifest',str(mf),
              '--lanes','2','--ua','test/1.0','--rps-start','10000','--rps-max','10000']
        old={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGINT)}
        try:
            with patch('sys.argv',argv), patch.object(kb,'CurlTransport',transport), patch.object(kb.cos_util,'creds',return_value=('test','test')), patch.object(kb.cos_util,'put',side_effect=lambda k,b:hashlib.md5(b).hexdigest()), contextlib.redirect_stdout(io.StringIO()):
                kb.main()
        finally:
            for s,h in old.items():signal.signal(s,h)
        return [json.loads(l) for l in mf.read_text().splitlines()]

    def test_concurrent_ledger_dedup_and_resume(self):
        class Transport:
            calls=0
            def __init__(self,*a):pass
            def fetch(self,u,p,h):
                Transport.calls+=1
                Path(p).write_bytes(b'\xff\xd8\xff'+b'bytes'*100)
                Path(h).write_text('HTTP/1.1 200 OK\r\n')
                return {'http_code':200,'curl_code':0,'time_total':.01}
            def close(self):pass
        with tempfile.TemporaryDirectory() as td:
            rows=self.run_pipeline(td,Transport)
            self.assertEqual(len(rows),100)
            self.assertEqual(len({(r['qid'],r['commons_file']) for r in rows}),100)
            self.assertTrue(all(r['miss'] is None and r['sha256'] for r in rows))
            self.run_pipeline(td,Transport)
            self.assertEqual(Transport.calls,100)

    def test_lane_exception_fails_process(self):
        class Broken:
            def __init__(self,*a):pass
            def fetch(self,*a):raise OSError('simulated transport failure')
            def close(self):pass
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):self.run_pipeline(td,Broken)

    def test_html_never_uploaded(self):
        class HTML:
            def __init__(self,*a):pass
            def fetch(self,u,p,h):
                Path(p).write_bytes(b'<html>error page</html>');Path(h).write_text('')
                return {'http_code':200,'curl_code':0,'time_total':.01}
            def close(self):pass
        with tempfile.TemporaryDirectory() as td:
            rows=self.run_pipeline(td,HTML)
            self.assertTrue(all(r['miss']=='not_image' and not r.get('sha256') for r in rows))


if __name__=='__main__':unittest.main()
