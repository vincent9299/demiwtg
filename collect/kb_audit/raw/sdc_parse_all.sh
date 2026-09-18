#!/bin/bash
# 串接 COS 上的 57 个 mediainfo 块 -> lbzip2 并行解压 -> grep 预滤 -> python 抽取
D=/lhcos-data/demiwtg-data/datasets/raw/wikimedia
for ((i=0;i<57;i++)); do printf "%s/latest-mediainfo.json.bz2.part-%05d\n" "$D" "$i"; done | xargs cat | \
  lbzip2 -d -c 2>/dev/null | \
  grep -a '"P180"' | \
  python3 /home/ubuntu/demi/raw/sdc_extract.py
