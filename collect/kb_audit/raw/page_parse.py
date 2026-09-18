#!/usr/bin/env python3
# 解析 commonswiki page.sql.gz -> page_id/page_title 映射（仅 File: 命名空间=6）
# MediaInfo M-id = "M" + page_id，所以这表给出 M-id <-> 文件名
import sys, re, gzip, time
sys.path.insert(0, "/home/ubuntu/demi/raw")
from cos_cat import stream_object  # 复用 COS 直读

KEY = "demiwtg-data/datasets/raw/wikimedia/commonswiki-latest-page.sql.gz.part-%05d"
out = gzip.open("/home/ubuntu/demi/raw/mid_to_file.tsv.gz", "wt", compresslevel=1)
# INSERT INTO `page` VALUES (id,ns,'title',... 解析 ns=6 的行
row_re = re.compile(r"\((\d+),(\d+),'((?:[^'\\\\]|\\\\.)*)',")
n = rows = 0
t0 = time.time()
buf = ""
import io

class PipeReader(io.RawIOBase):
    def __init__(self): self.gen = stream_object(KEY % i for i in range(7))
    def read(self, sz=-1):
        try: return next(self.gen)
        except StopIteration: return b""

for chunk in (stream_object(KEY % i) for i in range(7)):
    buf += chunk.decode("utf-8", errors="replace")
    n += len(chunk)
    # 只保留最后一行残余
    while True:
        m = row_re.search(buf)
        if not m: break
        pid, ns, title = m.group(1), m.group(2), m.group(3)
        buf = buf[m.end():]
        if ns == "6":
            # SQL 转义还原: \' -> ', \\ -> \, 其余常见
            t = title.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
            out.write(f"M{pid}\t{t}\n")
            rows += 1
            if rows % 2000000 == 0:
                out.flush(); print(f"[{time.strftime('%H:%M:%S')}] {rows/1e6:.0f}M files, {n/1e9:.1f}GB read", flush=True)
    if len(buf) > 4 << 20: buf = buf[-(1 << 20):]
out.close()
print(f"DONE file_rows={rows} read={n/1e9:.1f}GB", flush=True)
