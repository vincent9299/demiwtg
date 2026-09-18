#!/usr/bin/env python3
"""审计联合第二步(2026-09-17): 嗅探结果 × 账本 → 精确污染行清单+汇总报告。

前置: 8 片嗅探完成(state/sniffout_0*.tsv 每行 rel\tclass\terr);
可选: state/sniff_large.tsv(>6KB 随机样本, 上界验证)。
产出(raw/state/):
- poison_html_rows.jsonl.gz  HTML 错误页行(重收清单①, 行内附 err_code)
- poison_other_rows.jsonl.gz empty/missing/err 类异常行(重收清单③)
- audit_final_stats.json    全部计数(分档/分时/错误码/去重任务数)
- audit_report.md           人读报告
"""
import glob
import gzip
import json
import datetime
from collections import defaultdict

ST = "/home/ubuntu/demi/raw/state/"
LED = ST + "qid_images.jsonl"
SMALL_MAX = 6000

sniff = {}
for p in sorted(glob.glob(ST + "sniffout_0*.tsv")):
    with open(p, encoding="utf-8") as f:
        for line in f:
            rel, cls, err = line.rstrip("\n").split("\t")
            sniff[rel] = (cls, err)
print(f"嗅探结果载入: {len(sniff):,} 唯一 blob")

large = {}
try:
    with open(ST + "sniff_large.tsv", encoding="utf-8") as f:
        for line in f:
            rel, cls, err = line.rstrip("\n").split("\t")
            large[rel] = (cls, err)
except FileNotFoundError:
    pass

inv = {}
with open(ST + "blobs_inventory.tsv", encoding="utf-8") as f:
    for line in f:
        rel, sz = line.rstrip("\n").rsplit("\t", 1)
        if not rel.endswith("/"):
            inv[rel] = int(sz)

rows = badjson = 0
html_rows = other_rows = 0
html_bytes = other_bytes = 0
tasks_poison = set()
cls_cnt = defaultdict(int)
err_cnt = defaultdict(int)
tier_html = defaultdict(int)
decile_html = defaultdict(int)
day_html = defaultdict(int)
day_rows = defaultdict(int)
uniq_html_sha = set()
ext_html = defaultdict(int)

f_h = gzip.open(ST + "poison_html_rows.jsonl.gz", "wt", encoding="utf-8")
f_o = gzip.open(ST + "poison_other_rows.jsonl.gz", "wt", encoding="utf-8")

with open(LED, encoding="utf-8") as f:
    for idx, line in enumerate(f):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            badjson += 1
            continue
        rows += 1
        rel = r["path"]
        pb = r.get("page_bytes") or 0
        day = datetime.datetime.fromtimestamp(
            r.get("fetched_at") or 0,
            datetime.timezone.utc).strftime("%m-%d")
        day_rows[day] += 1
        got = sniff.get(rel)
        if got is not None:
            cls, err = got
            cls_cnt[cls] += 1
            if cls == "html":
                html_rows += 1
                html_bytes += pb
                tasks_poison.add((r["qid"], r["commons_file"]))
                uniq_html_sha.add(r["sha256"])
                tier_html[r.get("tier")] += 1
                decile_html[min(idx * 10 // 8861354, 9)] += 1
                day_html[day] += 1
                ext_html[r.get("ext")] += 1
                if err:
                    err_cnt[err] += 1
                f_h.write(json.dumps({**r, "err_code": err},
                                     ensure_ascii=False) + "\n")
            elif cls in ("empty", "missing", "err"):
                other_rows += 1
                other_bytes += pb
                tasks_poison.add((r["qid"], r["commons_file"]))
                f_o.write(json.dumps({**r, "sniff": cls},
                                     ensure_ascii=False) + "\n")

f_h.close()
f_o.close()

ok_small = sum(c for k, c in cls_cnt.items()
               if k not in ("html", "empty", "missing", "err"))
large_html = sum(1 for cls, _ in large.values() if cls == "html")
stats = {
    "ledger_rows": rows, "badjson": badjson,
    "sniffed_unique_blobs": len(sniff),
    "small_class_counts": dict(cls_cnt),
    "html_rows": html_rows, "html_bytes": html_bytes,
    "html_unique_shas": len(uniq_html_sha),
    "html_err_codes": dict(sorted(err_cnt.items(), key=lambda x: -x[1])),
    "html_by_tier": dict(tier_html),
    "html_by_ext": dict(sorted(ext_html.items(), key=lambda x: -x[1])[:10]),
    "html_by_ledger_decile": {str(k): v for k, v in sorted(decile_html.items())},
    "html_by_day_utc": {k: f"{v}/{day_rows[k]}" for k, v in
                        sorted(day_html.items())},
    "other_rows": other_rows, "other_bytes": other_bytes,
    "redownload_tasks_unique_qid_file": len(tasks_poison),
    "large_sample": {"checked": len(large), "html": large_html},
    "note_small_threshold_bytes": SMALL_MAX,
}
with open(ST + "audit_final_stats.json", "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=1)

lines = ["# kb 图池审计·终报(2026-09-17)", ""]
for k, v in stats.items():
    lines.append(f"- **{k}**: {v}")
lines += ["", ">6KB 档为抽样验证(非全量);重收清单: "
          "poison_html_rows.jsonl.gz / poison_other_rows.jsonl.gz / "
          "thumb1200_rows.jsonl.gz"]
with open(ST + "audit_report.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(json.dumps(stats, ensure_ascii=False, indent=1))
