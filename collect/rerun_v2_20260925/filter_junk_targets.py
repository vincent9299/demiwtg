#!/usr/bin/env python3
"""目标类过滤 · 在 si 修账版账本上删除「目标为页面/数字/年份类实体」的挂载边.

规则(用户裁定): 目标 QID 的 P31 主类 ∈ JUNK_MAINS → 该 qid-图 边不入账.
对全部来源统一执行(对已清过的 si 行幂等); refs 不动(记录的是抓取来源);
行级审计可回滚. 产出 images.v2.clean.jsonl.gz(行数与顺序保持).

JUNK_MAINS 共 11 类:
  页面系: Q4167410 消歧义页 / Q22808320 人名消歧义页 / Q15633587 MediaWiki 页面
  数字系: Q16317911 正整数 / Q125577 自然数 / Q11563 数 / Q49008 质数
  年份系: Q577 年 / Q3186692 年份 / Q3311614 跨世纪年
  其他:   Q101352 姓氏
"""
import collections
import gzip
import json
import subprocess
import sys
import time
from multiprocessing import Pool

BASE = "/yzp/zhaozy/yangzepeng/0905"
P31 = f"{BASE}/demiwtg/collect/sample_1m_transfer/assets/p31_all.tsv"
SRC = f"{BASE}/demiwtg/collect/rerun_v2_20260925/images.v2.si_rematch.jsonl.gz"
OUT_DIR = f"{BASE}/demiwtg/collect/rerun_v2_20260925"
OUT = f"{OUT_DIR}/images.v2.clean.jsonl.gz"
CHUNK = 32 << 20
NPROC = 48

JUNK_MAINS = {"Q4167410", "Q22808320", "Q15633587", "Q16317911", "Q125577",
              "Q11563", "Q49008", "Q577", "Q3186692", "Q3311614", "Q101352"}
JUNK_NUM = {m[1:] for m in JUNK_MAINS}      # p31_all 值为无 Q 前缀数字串

_junk = None


def _init(junk):
    global _junk
    _junk = junk


def process(chunk):
    """一个文本块: 删行内垃圾目标 qid, 返回(新文本, 统计, 审计行)."""
    st = collections.Counter()
    au = []
    out = []
    junk = _junk
    for line in chunk.splitlines():
        if not line:
            continue
        st["rows"] += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            st["bad"] += 1
            continue
        qids = r.get("qids") or []
        removed = [q for q in qids if q in junk]
        if not removed:
            out.append(line)
            continue
        st["rows_touched"] += 1
        st["edges_removed"] += len(removed)
        st[f"src_{r.get('source') or '?'}"] += 1
        new = [q for q in qids if q not in junk]
        if not new:
            st["rows_emptied"] += 1
        r["qids"] = new
        out.append(json.dumps(r, ensure_ascii=False))
        au.append(f"{r.get('sha256','')}\t{r.get('source') or '?'}\t"
                  f"{';'.join(removed)}\n")
    return "\n".join(out) + "\n", st, au


def main():
    t0 = time.time()
    junk = set()
    with open(P31) as f:
        for l in f:
            q, _, rest = l.partition("\t")
            if rest.split(",")[0].strip() in JUNK_NUM:
                junk.add(q)
    with open(f"{OUT_DIR}/junk_entities.tsv", "w") as f:
        for q in sorted(junk):
            f.write(q + "\n")
    print(f"[1] 垃圾目标实体 {len(junk):,} {time.time()-t0:.0f}s", flush=True)

    proc = subprocess.Popen(["pigz", "-dc", SRC], stdout=subprocess.PIPE,
                            bufsize=1 << 24)

    def gen():
        rem = b""
        while True:
            blk = proc.stdout.read(CHUNK)
            if not blk:
                break
            if rem:
                blk = rem + blk
            i = blk.rfind(b"\n")
            if i < 0:
                rem = blk
                continue
            rem, piece = blk[i + 1:], blk[:i]
            yield piece.decode("utf-8")
        if rem.strip():
            yield rem.decode("utf-8")

    total = collections.Counter()
    audit = open(f"{OUT_DIR}/filter_audit.tsv", "w")
    audit.write("sha256\tsource\tremoved_qids\n")
    with gzip.open(OUT, "wt", compresslevel=6) as out, Pool(NPROC, _init, (junk,)) as pool:
        for text, st, au in pool.imap(process, gen(), chunksize=1):   # imap 保序
            out.write(text)
            total.update(st)
            audit.writelines(au)
    proc.stdout.close()
    proc.wait()
    audit.close()
    total = dict(total)
    with open(f"{OUT_DIR}/filter_stats.json", "w") as f:
        json.dump(total, f, indent=1)
    print(json.dumps({k: v for k, v in sorted(total.items())}, indent=1))
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
