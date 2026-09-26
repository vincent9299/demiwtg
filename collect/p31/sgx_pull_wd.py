import os, re, sys, time
sys.path.insert(0, "/home/ubuntu/demiflow_collect")
from cosio import COSCreds, COSIO, build_host
io = COSIO(COSCreds.discover(paths=("/home/ubuntu/.cos_creds",)),
           build_host("lhcos-368f6-1256345599", "ap-singapore"))
PFX = "lhcos-data/demiwtg-data/kb/wd_full/parts/"
keys = [k for k in io.list_prefix(PFX) if "_p" in k]
def sk(k):
    m = re.search(r"m(\d+)_p(\d+)\.bin$", k)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 99)
keys.sort(key=sk)
print(f"parts: {len(keys)}", flush=True)
t0 = time.time(); n = 0
tmp = "/tmp/wdpart.bin"
with open("/home/ubuntu/latest-all.json.gz", "wb") as out:
    for k in keys:
        for attempt in range(5):
            try:
                if not io.download_to(k, tmp):
                    raise RuntimeError("dl false")
                with open(tmp, "rb") as f:
                    while True:
                        b = f.read(64 << 20)
                        if not b: break
                        out.write(b)
                os.unlink(tmp)
                n += 1
                if n % 25 == 0:
                    print(f"  {n}/{len(keys)} {out.tell()/1e9:.1f}GB {time.time()-t0:.0f}s", flush=True)
                break
            except Exception as e:
                print(f"  retry {k}: {e}", flush=True)
                time.sleep(10 * (attempt + 1))
        else:
            print(f"FAIL {k}"); sys.exit(3)
print(f"ASSEMBLED {out.tell():,} bytes in {time.time()-t0:.0f}s", flush=True)
