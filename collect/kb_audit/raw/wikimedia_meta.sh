#!/bin/bash
# Wikimedia 双包：SDC mediainfo（P180 depicts 桥）+ Commons image 表（sha1/尺寸预筛）
# 优先 your.org 镜像，失败回落官方；带 UA；流式直写 COS
fetch() {
  local name=$1; local path=$2; local out="/lhcos-data/demiwtg-data/datasets/raw/wikimedia/$name"
  local got=0
  for host in "https://dumps.wikimedia.your.org" "https://dumps.wikimedia.org"; do
    local expect=$(curl -sI -A "ConceptKB/1.0 (mengdebin@bytedance.com)" "$host$path" --max-time 30 | grep -i content-length | tr -dc '0-9')
    [ -z "$expect" ] && continue
    for i in 1 2 3 4 5 6 7 8; do
      curl -C - -A "ConceptKB/1.0 (mengdebin@bytedance.com)" -o "$out" --connect-timeout 30 --speed-limit 20480 --speed-time 90 "$host$path" && break
      echo "[$(date +%T)] $name retry#$i @$host"; sleep 15
    done
    local sz=$(stat -c%s "$out" 2>/dev/null || echo 0)
    if [ "$sz" = "$expect" ]; then echo "[$(date +%T)] $name DONE $sz"; got=1; break; else echo "[$(date +%T)] $name mismatch $sz/$expect, try next host"; rm -f "$out"; fi
  done
  [ $got = 0 ] && echo "[$(date +%T)] $name FAILED"
}
mkdir -p /lhcos-data/demiwtg-data/datasets/raw/wikimedia
fetch "latest-mediainfo.json.bz2" "/commonswiki/entities/latest-mediainfo.json.bz2"
fetch "commonswiki-latest-image.sql.gz" "/commonswiki/latest/commonswiki-latest-image.sql.gz"
echo ALL_DONE
