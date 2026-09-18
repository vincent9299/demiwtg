#!/usr/bin/env python3
# stdin <- page.sql 明文; 抽 ns=6 (File:) 的 (page_id,title) -> M<id>\t<title>
import sys, re, gzip, time
row_re = re.compile(r"\((\d+),(\d+),'((?:[^'\\\\]|\\\\.)*)',")
out = gzip.open("/home/ubuntu/demi/raw/mid_to_file.tsv.gz", "wt", compresslevel=1)
buf = ""
n = rows = 0
t0 = time.time()
for chunk in sys.stdin:
    buf += chunk
    n += len(chunk)
    while True:
        m = row_re.search(buf)
        if not m: break
        pid, ns, title = m.group(1), m.group(2), m.group(3)
        buf = buf[m.end():]
        if ns == "6":
            t = title.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
            out.write(f"M{pid}\t{t}\n"); rows += 1
            if rows % 2000000 == 0:
                out.flush(); print(f"[{time.strftime('%H:%M:%S')}] {rows/1e6:.0f}M files {n/1e9:.1f}GB", flush=True)
    if len(buf) > (4 << 20): buf = buf[-(1 << 20):]
out.close()
print(f"DONE file_rows={rows} read={n/1e9:.1f}GB rate={n/max(time.time()-t0,1)/1e6:.0f}MB/s", flush=True)
