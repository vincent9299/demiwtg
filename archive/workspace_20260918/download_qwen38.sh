#!/bin/bash
# 并行 curl 下载 Qwen3.8-27B（hf-mirror），带重试与大小校验
set -u
BASE="https://hf-mirror.com/Qwen/Qwen3.8-27B/resolve/main"
DEST="/yzp/zhaozy/yangzepeng/0905/models/Qwen3.8-27B"
LIST="/tmp/kilo/qwen38_files.txt"
mkdir -p "$DEST"

# 从 API 生成 "size path" 清单（仅主目录文件）
curl -sL "https://hf-mirror.com/api/models/Qwen/Qwen3.8-27B/tree/main" | \
  python3 -c "
import json,sys
for f in json.load(sys.stdin):
    if f['type']=='file' and not f['path'].startswith('.'):
        print(f['size'], f['path'])
" > "$LIST"

download_one() {
  local size="$1" path="$2"
  local out="$DEST/$path"
  for attempt in 1 2 3 4 5; do
    local cur=0
    [ -f "$out" ] && cur=$(stat -c %s "$out")
    if [ "$cur" = "$size" ]; then echo "OK  $path"; return 0; fi
    curl -sL --retry 3 --speed-time 30 --speed-limit 10000 \
      -o "$out" "$BASE/$path" && cur=$(stat -c %s "$out" 2>/dev/null || echo 0)
    if [ "$cur" = "$size" ]; then echo "OK  $path"; return 0; fi
    echo "RETRY($attempt) $path ($cur/$size)"; sleep 3
  done
  echo "FAIL $path"; return 1
}
export -f download_one
export BASE DEST

awk '{print $1, $2}' "$LIST" | xargs -P 6 -n2 bash -c 'download_one "$@"' _
echo "ALL_DONE failures=$(awk '{print $2}' "$LIST" | while read p; do [ "$(stat -c %s "$DEST/$p" 2>/dev/null)" != "$(grep " $p\$" "$LIST" | awk "{print \$1}")" ] && echo "$p"; done | wc -l)"
