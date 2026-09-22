"""Synchronous libcurl sessions: one reusable connection pool per download lane."""
import json
import subprocess


class CurlTransport:
    def __init__(self, proxy, headers, timeout, cap, backend="pycurl"):
        self.proxy, self.headers = proxy, headers
        self.timeout, self.cap, self.backend = timeout, cap, backend
        self.handle = None
        if backend == "pycurl":
            import pycurl
            self.pc = pycurl
            c = self.handle = pycurl.Curl()
            c.setopt(c.PROXY, proxy)  # Empty explicitly means direct, ignoring ambient proxies.
            c.setopt(c.FOLLOWLOCATION, 1)
            c.setopt(c.MAXREDIRS, 5)
            c.setopt(c.CONNECTTIMEOUT, 15)
            c.setopt(c.TIMEOUT, timeout)
            c.setopt(c.NOSIGNAL, 1)
            c.setopt(c.MAXFILESIZE_LARGE, cap)
            c.setopt(c.USERAGENT, headers[headers.index("-A") + 1])
            if "-e" in headers:
                c.setopt(c.REFERER, headers[headers.index("-e") + 1])

    def fetch(self, url, path, header_path):
        if self.handle is None:
            cmd = ["curl", "-sSL", "--proxy", self.proxy, "--connect-timeout", "15",
                   "--max-time", str(self.timeout), "--max-filesize", str(self.cap),
                   "-D", header_path] + self.headers + ["-w", "%{json}", "-o", path, url]
            try:
                r = subprocess.run(cmd, timeout=self.timeout + 15, capture_output=True, text=True)
                try:
                    d = json.loads(r.stdout)
                except ValueError:
                    d = {}
                return {k: d.get(k, 0) for k in (
                    "http_code", "time_connect", "time_appconnect", "time_starttransfer",
                    "time_total", "size_download", "num_connects")} | {"curl_code": r.returncode}
            except subprocess.TimeoutExpired:
                return {"http_code": 0, "curl_code": 28, "time_total": self.timeout + 15}
        c = self.handle
        size = 0
        with open(path, "wb") as body, open(header_path, "wb") as hdr:
            def write(chunk):
                nonlocal size
                size += len(chunk)
                if size > self.cap:
                    return 0
                return body.write(chunk)
            c.setopt(c.URL, url)
            c.setopt(c.WRITEFUNCTION, write)
            c.setopt(c.HEADERFUNCTION, hdr.write)
            error = 0
            try:
                c.perform()
            except self.pc.error as e:
                error = e.args[0]
            return {k: c.getinfo(v) for k, v in (
                ("http_code", c.RESPONSE_CODE), ("time_connect", c.CONNECT_TIME),
                ("time_appconnect", c.APPCONNECT_TIME), ("time_starttransfer", c.STARTTRANSFER_TIME),
                ("time_total", c.TOTAL_TIME), ("size_download", c.SIZE_DOWNLOAD),
                ("num_connects", c.NUM_CONNECTS))} | {"curl_code": error}

    def close(self):
        if self.handle is not None:
            self.handle.close()
