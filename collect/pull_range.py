"""r 机上运行: COS Range GET -> stdout. argv: key start end"""
import sys
sys.path.insert(0, "/tmp/ci")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=("/tmp/cos_creds", "/home/ubuntu/wk_backfill/.cos_creds")),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
key, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
import urllib.parse
for attempt in range(3):
    try:
        st, h, body = io._request("GET", urllib.parse.quote("/" + key.lstrip("/")),
                                  {}, None, 600.0,
                                  extra_headers={"Range": f"bytes={a}-{b}"})
        if st in (200, 206) and len(body) == b - a + 1:
            sys.stdout.buffer.write(body)
            sys.exit(0)
    except Exception:
        pass
    import time; time.sleep(5 * (attempt + 1))
sys.exit(f"FAIL {key} {a}-{b}")
