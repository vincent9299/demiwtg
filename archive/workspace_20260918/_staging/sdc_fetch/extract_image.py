#!/usr/bin/env python3
"""SDC fetch ①-b: Commons image 表预筛抽取器(跑在 pipeline-b)。v2

v1 教训:该 dump 用**反斜杠转义**(`\'` `\"` `\\`),不是 `''` 双写;
名字或 EXIF 元数据带撇号的行(约 5.8%)会被 v1 丢掉。v2 用转义感知
扫描找字符串闭引号,并对输出名做 SQL 反转义(与 mid_to_file 口径一致)。

stdin: cat commonswiki-latest-image.sql.gz.part-00000..00017 | gzip -dc
stdout: fname<TAB>size,仅保留 img_media_type=BITMAP 且 51200<=size<=64MB。
img_name 为 varbinary,全程按 bytes 处理,与 mid_to_file 同用下划线口径。

列序(image.sql 2026-09 dump 实测):
  0 img_name | 1 img_size | 2 img_width | 3 img_height
  4 img_metadata(巨 JSON 串,含转义,整段跳过) | 5 img_bits
  6 img_media_type 'BITMAP'|NULL | ...
"""
import re
import sys
import time

LO, HI = 51200, 64 << 20
T0 = time.time()

re_nums = re.compile(rb",(\d+),(\d+),(\d+),")
re_tail = re.compile(rb",(\d+),(?:'([A-Z]+)'|NULL)")
re_unesc = re.compile(rb"\\(.)")
_UNESC = {b"'": b"'", b'"': b'"', b"\\": b"\\", b"n": b"\n",
          b"r": b"\r", b"0": b"\0", b"Z": b"\x1a"}


def find_close(ln, open_idx):
    """open_idx 指向开引号;返回闭引号下标或 -1。

    混合扫描保 v1 速度:普通 find 直定位 `'`,仅对命中点做两种转义判定——
    `''` 双写(后随引号)与 `\'`(前置反斜杠,数奇偶);JSON 里的 `\"` 与
    本函数无关,find 根本不停。逐 token 正则版正确但慢 6.6 倍,弃。
    """
    pos = open_idx
    while True:
        j = ln.find(b"'", pos + 1)
        if j == -1:
            return -1
        if ln[j + 1:j + 2] == b"'":          # '' 双写转义
            pos = j + 1
            continue
        k = j - 1
        nb = 0
        while k >= 0 and ln[k] == 0x5C:       # 前置反斜杠个数
            nb += 1
            k -= 1
        if nb % 2 == 0:
            return j
        pos = j                               # \' 转义引号,继续


def unescape(s):
    if b"\\" not in s:
        return s
    return re_unesc.sub(lambda m: _UNESC.get(m.group(1), m.group(0)), s)


n_tuple = n_bitmap = n_out = n_unsorted = n_bad = 0
prev = b""
out = sys.stdout.buffer

for ln in sys.stdin.buffer:
    if not ln.startswith(b"('"):
        continue
    n_tuple += 1
    j = find_close(ln, 1)                 # field0: img_name
    if j == -1:
        n_bad += 1
        continue
    m = re_nums.match(ln, j + 1)          # ,SIZE,W,H,
    if not m:
        n_bad += 1
        continue
    size = int(m.group(1))
    q = m.end()
    if ln[q:q + 1] != b"'":               # field4: img_metadata 开引号
        n_bad += 1
        continue
    e = find_close(ln, q)                 # 跳过整个 metadata
    if e == -1:
        n_bad += 1
        continue
    m2 = re_tail.match(ln, e + 1)         # field5 bits + field6 mediatype
    if not m2:
        n_bad += 1
        continue
    if m2.group(2) == b"BITMAP":
        n_bitmap += 1
        if LO <= size <= HI:
            name = unescape(ln[2:j])
            if name < prev:
                n_unsorted += 1
            prev = name
            n_out += 1
            out.write(name + b"\t" + str(size).encode() + b"\n")
    if n_tuple % 10_000_000 == 0:
        el = time.time() - T0
        print(f"[extract] {n_tuple:,} tuples {n_tuple/el:,.0f}/s "
              f"bitmap={n_bitmap:,} out={n_out:,} bad={n_bad} "
              f"unsorted={n_unsorted}", file=sys.stderr, flush=True)

el = time.time() - T0
print(f"[extract] DONE tuples={n_tuple:,} bitmap={n_bitmap:,} "
      f"out={n_out:,} bad={n_bad} unsorted={n_unsorted} "
      f"elapsed={el/60:.1f}min", file=sys.stderr, flush=True)
