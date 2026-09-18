#!/usr/bin/env python3
"""COS 直连 inventory: 匿名 list-type=2 翻页列出 kb/blobs 全部对象键+大小,
绕开 cosfs 低速随机读。输出 TSV: rel_path\tsize (rel 相对 kb 根)。

用法: python3 cos_inventory.py <out.tsv> [并发子前缀数]
"""
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET

BUCKET = "lhcos-368f6-1256345599"
REGION = "ap-singapore"
HOST = f"{BUCKET}.cos.{REGION}.myqcloud.com"
BASE_PREFIX = "lhcos-data/demiwtg-data/datasets/demiwtg/kb/"
SUBS = [f"{i:02x}" for i in range(256)]


def list_prefix(prefix, out, lock):
    """单子前缀翻页到底; 返回 (keys, bytes)。"""
    token, n, total = None, 0, 0
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            q["continuation-token"] = token
        url = f"https://{HOST}/?{urllib.parse.urlencode(q)}"
        for attempt in range(8):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    body = r.read()
                break
            except Exception as e:
                if attempt == 7:
                    raise RuntimeError(f"list {prefix} 失败: {e}")
                time.sleep(2 * (attempt + 1))
        root = ET.fromstring(body)
        with lock:
            for c in root.iter("Contents"):
                key = c.findtext("Key")
                size = int(c.findtext("Size"))
                out.write(f"{key[len(BASE_PREFIX):]}\t{size}\n")
                n += 1
                total += size
        tok = root.findtext("NextContinuationToken")
        if root.findtext("IsTruncated") != "true" or not tok:
            return n, total
        token = tok


def main():
    out_path = sys.argv[1]
    par = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    import threading
    lock = threading.Lock()
    t0 = time.time()
    with open(out_path, "w", encoding="utf-8") as out:
        with ThreadPoolExecutor(par) as ex:
            futs = [ex.submit(list_prefix,
                              f"{BASE_PREFIX}blobs/{s}/", out, lock)
                    for s in SUBS]
            done = 0
            for i, fu in enumerate(futs):
                n, b = fu.result()
                done += 1
                if done % 32 == 0:
                    el = time.time() - t0
                    print(f"[inv] {done}/256 子前缀完成 elapsed={el:.0f}s",
                          flush=True)
    print(f"INVENTORY_DONE -> {out_path} ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
