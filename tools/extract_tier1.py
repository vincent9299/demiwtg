#!/usr/bin/env python
"""档1补充图提取器:流式重放 truthy 拼接流,一次抽出全部图像资产引用。

输出(/tmp/tier1/):
  qid-images.tsv.gz   (qid, pid, role, filename)  全部图像属性(非音频/视频/3D)
  p935-titles.tsv.gz  (qid, gallery_title)        图库页标题(API 展开用)
仅保留概念集(7.83M)内的主体;文件名两步还原(URL解码+下划线转空格)。
"""
import gzip
import os
import re
import sys

OUT_DIR = "/tmp/tier1"
CONCEPTS = os.path.expanduser(
    "~/demi/kb_night/qid_build/qid_concepts.jsonl")

IMG_EXT = {".jpg", ".jpeg", ".png", ".svg", ".tif", ".tiff", ".gif", ".webp"}
SKIP_PID = {"P51", "P443", "P989", "P10", "P4896", "P373"}
ROLE = {
    "P18": "representative", "P41": "flag", "P94": "coat_of_arms",
    "P158": "seal", "P154": "logo", "P1621": "logo_detailed",
    "P8972": "logo_small", "P109": "signature", "P117": "chem_structure",
    "P2716": "collage", "P181": "range_map", "P242": "locator_map",
    "P1846": "distribution_map", "P1442": "grave", "P1801": "monument",
    "P3451": "night_view", "P5252": "winter_view", "P2910": "icon",
    "P4291": "panorama", "P8592": "aerial_view", "P14": "traffic_sign",
    "P207": "bathymetry_map", "P15": "route_map", "P935": "gallery",
}

SUB_RE = re.compile(r"^<http://www\.wikidata\.org/entity/(Q\d+)>\s+"
                    r"<http://www\.wikidata\.org/prop/direct/(P\d+)>\s+(.+?)\s*\.\s*$")
FP_RE = re.compile(r"^<http://commons\.wikimedia\.org/wiki/"
                   r"Special:FilePath/([^>]+)>$")
LIT_RE = re.compile(r'^"(.*)"$')

from urllib.parse import unquote


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    concepts = set()
    import json as _json
    opener = gzip.open if CONCEPTS.endswith(".gz") else open
    with opener(CONCEPTS, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                r = _json.loads(line)
            except Exception:
                continue
            q = r.get("qid") if isinstance(r, dict) else r
            if q:
                concepts.add(q)
    sys.stderr.write(f"[tier1] concepts loaded: {len(concepts)}\n")

    n_img = n_935 = n_skip_pid = n_nonconcept = 0
    with gzip.open(f"{OUT_DIR}/qid-images.tsv.gz", "wt", encoding="utf-8") as fo, \
         gzip.open(f"{OUT_DIR}/p935-titles.tsv.gz", "wt", encoding="utf-8") as f9:
        for line in sys.stdin:
            m = SUB_RE.match(line)
            if not m:
                continue
            qid, pid, obj = m.groups()
            if qid not in concepts:
                n_nonconcept += 1
                continue
            fp = FP_RE.match(obj)
            if fp:
                if pid in SKIP_PID:
                    n_skip_pid += 1
                    continue
                fname = unquote(fp.group(1)).replace("_", " ")
                if os.path.splitext(fname)[1].lower() not in IMG_EXT:
                    n_skip_pid += 1      # 音/视/3D/pdf 等非图扩展
                    continue
                fo.write(f"{qid}\t{pid}\t{ROLE.get(pid, 'other_image')}\t{fname}\n")
                n_img += 1
            elif pid == "P935":
                lit = LIT_RE.match(obj)
                if lit and lit.group(1).strip():
                    f9.write(f"{qid}\t{lit.group(1)}\n")
                    n_935 += 1
    sys.stderr.write(f"[tier1] img_rows={n_img} p935={n_935} "
                     f"skip_pid_or_ext={n_skip_pid} nonconcept_lines={n_nonconcept}\n")


if __name__ == "__main__":
    main()
