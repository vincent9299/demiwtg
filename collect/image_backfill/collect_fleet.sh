#!/bin/bash
# fleet 产出自动回收循环：完成的机器 → tar校验 → 拉回 → 解包导入 → 标记
# 用法: setsid nohup bash collect_fleet.sh >> collect_fleet.log 2>&1 < /dev/null &
BASE=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1
IN=$BASE/fleet_in
PY=/yzp/zhaozy/yangzepeng/0905/env/bin/python
IMP=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/image_backfill/import_blobs.py
mkdir -p $IN

collect_one () {
  local h=$1 i=$2
  [ -f $IN/$h/imported.ok ] && return 0
  # 远端是否已 DONE（watchdog 记录）
  timeout 30 ssh -o ConnectTimeout=15 $h "grep -q DONE ~/wk_backfill/run_${i}.watchdog.log 2>/dev/null" || return 1
  echo "[$(date +%H:%M)] $h DONE, 打包"
  timeout 30 ssh -o ConnectTimeout=15 $h "cd ~/wk_backfill && setsid nohup bash -c 'tar cf /tmp/out.tar run_${i} domrun_${i} 2>/dev/null; tar tf /tmp/out.tar >/dev/null 2>&1 && touch /tmp/out.tar.ok' >/dev/null 2>&1 < /dev/null"
  for try in 1 2 3 4 5 6 7 8 9 10; do
    sleep 60
    timeout 30 ssh -o ConnectTimeout=15 $h "test -f /tmp/out.tar.ok" 2>/dev/null && break
  done
  timeout 30 ssh -o ConnectTimeout=15 $h "test -f /tmp/out.tar.ok" 2>/dev/null || { echo "$h tar 校验失败"; return 1; }
  mkdir -p $IN/$h
  echo "[$(date +%H:%M)] $h 拉回中"
  rsync -a --append --timeout=180 $h:/tmp/out.tar $IN/$h/out.tar || return 1
  echo "[$(date +%H:%M)] $h 解包导入"
  mkdir -p $IN/$h/x
  tar xf $IN/$h/out.tar -C $IN/$h/x 2>>$IN/$h/extract.err || { echo "$h 解包失败"; return 1; }
  $PY $IMP $IN/$h/x/run_${i} $IN/$h/x/run_${i}/meta/done.jsonl $IN/$h/x/domrun_${i}/meta/done.jsonl > $IN/$h/import.log 2>&1
  tail -1 $IN/$h/import.log
  touch $IN/$h/imported.ok
  # 清理两端大文件
  rm -f $IN/$h/out.tar
  timeout 30 ssh -o ConnectTimeout=15 $h "rm -f /tmp/out.tar /tmp/out.tar.ok" 2>/dev/null
  echo "[$(date +%H:%M)] $h 回收完成"
}

while true; do
  all=1
  for k in $(seq 0 19); do
    h=r$((k+1)); i=$(printf "%02d" $k)
    [ -f $IN/$h/imported.ok ] && continue
    collect_one $h $i &   # 并行回收（受带宽约束）
    all=0
  done
  wait
  [ $all -eq 1 ] && { echo "[$(date +%H:%M)] 全部机器回收完成"; break; }
  sleep 600
done
