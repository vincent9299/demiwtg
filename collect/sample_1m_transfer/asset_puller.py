#!/usr/bin/env python3
"""Pull relay1m/_assets/* from GZ to local assets/ (rounds, size-verified)."""
import importlib.util, sys, os, time
_spec = importlib.util.spec_from_file_location("cosio", "/yzp/zhaozy/yangzepeng/0905/demiflow/demiflow/collect/cosio.py")
cosio = importlib.util.module_from_spec(_spec); sys.modules["cosio"] = cosio; _spec.loader.exec_module(cosio)
_gz = cosio.COSIO(cosio.COSCreds.from_file("/tmp/cos_creds"), cosio.build_host("lhcos-cee54-1256345599", "ap-guangzhou"))
LOCAL = "/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer/assets"
PFX = "relay1m/_assets/"

for rnd in range(40):
    keys = _gz.list_prefix(PFX)
    pulled = skipped = 0
    for k in keys:
        rel = k[len(PFX):]
        lp = os.path.join(LOCAL, rel)
        sz = _gz.head(k)
        if sz is None:
            continue
        if os.path.exists(lp) and os.path.getsize(lp) == sz:
            skipped += 1
            continue
        body = None
        if sz and sz > 16 * 1024 * 1024:
            CHUNK = 32 * 1024 * 1024
            parts = []
            pos = 0
            okflag = True
            while pos < sz:
                for attempt in range(5):
                    try:
                        st, hd, chunk = _gz.call("GET", k, timeout=600.0, headers={"Range": f"bytes={pos}-{min(pos+CHUNK, sz)-1}"})
                        if st in (200, 206) and chunk:
                            parts.append(chunk)
                            pos += len(chunk)
                            break
                    except Exception as e:
                        print(f"[retry] {rel} @{pos//1048576}MB a{attempt} {str(e)[:50]}", flush=True)
                        time.sleep(5 * (attempt + 1))
                else:
                    okflag = False
                    break
            if okflag:
                body = b"".join(parts)
        else:
            for attempt in range(4):
                try:
                    st, hd, body = _gz.call("GET", k, timeout=900.0)
                    if st == 200:
                        break
                except Exception as e:
                    print(f"[retry] {rel} attempt{attempt} {str(e)[:60]}", flush=True)
                    body = None
                    time.sleep(10 * (attempt + 1))
        if body is not None and len(body) == sz:
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            with open(lp + ".part", "wb") as w:
                w.write(body)
            os.replace(lp + ".part", lp)
            pulled += 1
            print(f"[pull] {rel} {len(body)/1e6:.1f}MB", flush=True)
    print(f"[round {rnd}] remote={len(keys)} pulled={pulled} skipped={skipped}", flush=True)
    if pulled == 0 and rnd > 2:
        break
    time.sleep(300)
print("[asset END]", flush=True)
