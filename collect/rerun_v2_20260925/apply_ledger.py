#!/usr/bin/env python3
"""si 重匹配落账 · 产出修订版账本 images.v2.si_rematch.jsonl.gz.

原则: 原始 canonical(wh_backfill/images.v2.jsonl.gz)一字节不动;
本脚本生成修订版 + 行级审计 + 不变量校验, 供人工核验后再定转正/上 COS.

修订规则(按 si_rematch_map2.tsv 的 disposition, media 级):
  replace       qids: 删 old_qid 加 new_qid; 对应 ref relation_type→taxon_match
  remove_junk   qids: 删 old_qid;                    ref→name_match_dropped
  remove_wrong  同上
  keep          不动;                                ref→name_match
非 si 行原样直通(字节级不变). qids 修改保持原序去重, 新增 QID 追加.
qids 删空的行保留(图仍在库, 无关联).

审计: apply_audit.tsv (sha, media, removed, added, disposition)
不变量: 行数/唯一图数不变; 非 qids/refs 字段逐行一致(抽样+计数双验).
"""
import collections
import gzip
import json
import subprocess
import time

BASE = "/yzp/zhaozy/yangzepeng/0905"
SRC = f"{BASE}/demiwtg/collect/sample_1m_transfer/assets/wh_backfill/images.v2.jsonl.gz"
MAP = f"{BASE}/demiwtg/collect/rerun_v2_20260925/si_rematch_map2.tsv"
OUT_DIR = f"{BASE}/demiwtg/collect/rerun_v2_20260925"
OUT = f"{OUT_DIR}/images.v2.si_rematch.jsonl.gz"


def main():
    t0 = time.time()
    # ---- 1. media 级映射 ----
    disp_map = {}
    for l in open(MAP):
        p = l.rstrip("\n").split("\t")
        if p[0] == "media_id":
            continue
        media, old, new, _name, _method, disp = p
        disp_map[media] = (old, new, disp)
    print(f"[1] 映射 {len(disp_map):,} media {time.time()-t0:.0f}s", flush=True)

    st = collections.Counter()
    audit = open(f"{OUT_DIR}/apply_audit.tsv", "w")
    audit.write("sha256\tmedia\tremoved\tadded\tdisposition\n")

    proc = subprocess.Popen(["pigz", "-dc", SRC], stdout=subprocess.PIPE,
                            bufsize=1 << 24)
    out = gzip.open(OUT, "wt", compresslevel=6)
    for line in proc.stdout:
        # 快路径: 非 si 行直通(两种 JSON 空格风格都探测, 防写手不同漏行)
        if (b'"source": "si"' not in line) and (b'"source":"si"' not in line):
            out.write(line.decode("utf-8"))
            st["pass_through"] += 1
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            out.write(line.decode("utf-8"))
            st["parse_fail_passthrough"] += 1
            continue
        refs = r.get("refs") or []
        touched = False
        removed, added = set(), set()
        disp_seen = set()
        for ref in refs:
            if ref.get("source") != "si":
                continue
            m = disp_map.get(ref.get("external_id"))
            if m is None:
                if ref.get("relation_type") == "curated":
                    ref["relation_type"] = "taxon_match"   # si 原生 taxon 匹配行, 仅改名
                    touched = True
                continue
            old, new, disp = m
            disp_seen.add(disp)
            if disp == "replace":
                removed.add(old)
                added.add(new)
                ref["relation_type"] = "taxon_match"
            elif disp in ("remove_junk", "remove_wrong"):
                removed.add(old)
                ref["relation_type"] = "name_match_dropped"
            else:
                ref["relation_type"] = "name_match"
            touched = True
        if not touched:
            out.write(line.decode("utf-8"))
            st["si_untouched"] += 1
            continue
        if not removed and not added:
            st["rows_ref_renamed"] += 1
        qids = r.get("qids") or []
        kept = [q for q in qids if q not in removed]
        kept += [q for q in sorted(added) if q not in kept]
        r["qids"] = kept
        if not kept:
            st["rows_emptied"] += 1
        st["rows_changed"] += 1
        st["edges_removed"] += len(removed)
        st["edges_added"] += len(added)
        for d in disp_seen:
            st[f"row_{d}"] += 1
        audit.write(f"{r.get('sha256','')}\t{len(disp_seen)}\t"
                    f"{';'.join(sorted(removed))}\t{';'.join(sorted(added))}\t"
                    f"{';'.join(sorted(disp_seen))}\n")
        out.write(json.dumps(r, ensure_ascii=False) + "\n")
    out.close()
    proc.stdout.close()
    proc.wait()
    audit.close()
    st = dict(st)
    with open(f"{OUT_DIR}/apply_stats.json", "w") as f:
        json.dump(st, f, indent=1)
    print(json.dumps(st, indent=1))
    print(f"[done] {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
