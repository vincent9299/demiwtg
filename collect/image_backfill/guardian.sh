#!/bin/bash
# 守护进程：图片补图全链路护航（2026-09-17 起 10 小时）
# 阶段：STOCK(空闲机存量直传COS腾盘，占用机跳过) -> DEPLOY(空闲机带节拍增量)
#       -> CRUISE(每10分钟巡检：进度/429/磁盘/质量抽检/占用机释放补投，自动冷却)
# 启动：setsid nohup bash guardian.sh >> $HUB/guardian.log 2>&1 < /dev/null &
set -u
HUB=/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1
STG=/yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill
GDIR=$HUB/guardian
mkdir -p $GDIR
START=$(date +%s)
DEADLINE=$((START + 10*3600))
PHASE=STOCK
CLEAR_STREAK=0
BASE_EXCLUDE=""   # r5/r13 已于 18:05 释放回归编队；动态占用检测仍在兜底
COOLS=0
CYCLE=0
ZERO_STREAK=0

log(){ echo "[$(date '+%m-%d %H:%M:%S')] [$PHASE] $*" >> $HUB/guardian.log; }

sq(){ timeout 40 ssh -o ConnectTimeout=15 -o BatchMode=yes "$1" "$2" 2>/dev/null; }

all_stats(){   # 每台一行: host load1 diskpct b2cnt curlcnt（tr 保留\n 防字段黏连）
  for i in $(seq 1 20); do
    h=r$i
    echo "$h $(sq $h 'cut -d" " -f1 /proc/loadavg; df --output=pcent / | tail -1 | tr -dc "0-9\n"; pgrep -fc "[b]2_inat"; true; pgrep -fc "[f]leet_curl"; true' | tr '\n' ' ')"
    sleep 1.5
  done
}

blockers(){   # 只有任务进程在跑或负载显著高才算占用（残留负载不挡道）
  all_stats | awk '($4+0>0)||($2+0>3){printf "%s ",$1}'; }

busy_hosts(){ blockers | tr ' ' '\n' | grep -c '^r' ; true; }
busy_list(){ blockers | tr ' ' '\n' | grep '^r' | paste -sd, ; }

global_cool(){   # 全 fleet 拉闸 + 冷却 + 幂等重部署
  COOLS=$((COOLS+1))
  log "!! 全局冷却 #$COOLS：$1"
  for i in $(seq 1 20); do sq r$i 'pkill -f "[f]leet_curl.py"; pkill -f "[c]os_stock_upload"'; sleep 1.5; done
  sleep 1800
  log "冷却完毕，重新部署（幂等）"
  python3 $STG/deploy_all.py >> $HUB/guardian.log 2>&1
  log "重部署完成"
}

quality_sample(){   # 抽 6 条最新 done 发给 quality_probe
  local picked=$GDIR/qs_$CYCLE.in
  : > $picked
  for i in $(seq 1 20); do
    sq r$i 'for f in ~/wk_backfill/run_ps*/meta/done.jsonl; do tail -n 1 "$f"; done 2>/dev/null | grep -v cos_only | tail -1' >> $picked
    sleep 1
    [ $(grep -c . $picked) -ge 6 ] && break
  done
  [ -s $picked ] && python3 $STG/quality_probe.py < $picked >> $GDIR/quality.log 2>&1
}

# ---------- 主循环 ----------
while true; do
  CYCLE=$((CYCLE+1))
  NOW=$(date +%s)
  if [ $NOW -gt $DEADLINE ]; then
    log "护航 10 小时到期。当前 workers 保留运行（有节拍），守护退出。COOLS=$COOLS"
    echo done > $GDIR/EXITED
    break
  fi

  if [ "$PHASE" = "STOCK" ]; then
    BUSY=$(busy_list); export SKIP_HOSTS="$BASE_EXCLUDE${BUSY:+,$BUSY}"
    log "部署存量直传（排除: $SKIP_HOSTS）"
    for i in $(seq 1 20); do
      h=r$i
      case ",$SKIP_HOSTS," in *",$h,"*) continue;; esac
      timeout 60 scp -q -o ConnectTimeout=15 $STG/cos_stock_upload.py $STG/cos_util.py $STG/.cos_creds $h:~/wk_backfill/ 2>/dev/null
      # 守卫与启动分两次 ssh：同命令行会让 pgrep 自匹配导致永远不启动
      GUARD=$(sq $h 'pgrep -fc "[c]os_stock_upload"; true')
      [ "${GUARD:-0}" -eq 0 ] 2>/dev/null && sq $h 'cd ~/wk_backfill && chmod 600 .cos_creds && setsid nohup python3 -u cos_stock_upload.py --purge --workers 3 > stock.log 2>&1 < /dev/null & echo launched'
      sleep 3
    done
    log "存量直传已在空闲机后台展开，与增量下载并行（互不抢带宽：COS 出 vs wikimedia 入）"
    PHASE=DEPLOY
    continue
  fi

  if [ "$PHASE" = "DEPLOY" ]; then
    BUSY=$(busy_list); export SKIP_HOSTS="$BASE_EXCLUDE${BUSY:+,$BUSY}"
    log "部署增量（28 出口 × AIMD × COS 直传；排除: $SKIP_HOSTS）"
    python3 $STG/deploy_all.py >> $HUB/guardian.log 2>&1
    log "部署完成，进入巡航巡检"
    PHASE=CRUISE
    continue
  fi

  if [ "$PHASE" = "CRUISE" ]; then
    STATS=$(all_stats)
    echo "$STATS" > $GDIR/stats_$CYCLE.txt
    TRIPS=0; DEAD429=0; DEADCNT=0; DONECNT=0; CURLS=0; LOWDISK=""
    for i in $(seq 1 20); do
      h=r$i
      t=$(sq $h 'find ~/wk_backfill -name rate.log -mmin -11 2>/dev/null | xargs grep -h "\"trip\"" 2>/dev/null | wc -l')
      TRIPS=$((TRIPS + ${t:-0}))
      d429=$(sq $h 'find ~/wk_backfill -name dead.jsonl -mmin -11 2>/dev/null | xargs grep -hc "http:429" 2>/dev/null | awk "{s+=\$1} END{print s+0}"')
      DEAD429=$((DEAD429 + ${d429:-0}))
      dc=$(sq $h 'cat ~/wk_backfill/run_ps*/meta/dead.jsonl 2>/dev/null | wc -l')
      DEADCNT=$((DEADCNT + ${dc:-0}))
      dn=$(sq $h 'cat ~/wk_backfill/run_ps*/meta/done.jsonl 2>/dev/null | wc -l')
      DONECNT=$((DONECNT + ${dn:-0}))
      c=$(sq $h 'pgrep -fc "[f]leet_curl"; true')
      CURLS=$((CURLS + ${c:-0}))
      p=$(echo "$STATS" | awk -v h=$h '$1==h{print $3}')
      [ "${p:-100}" -gt 92 ] 2>/dev/null && LOWDISK="$LOWDISK $h(${p}%)"
      sleep 1
    done
    log "巡检#$CYCLE workers=$CURLS done=$DONECNT dead=$DEADCNT 10min内429死信=$DEAD429 10min内trip=$TRIPS 低盘:$LOWDISK"
    quality_sample
    # 质量问题摘要（非 OK verdict）
    BADQ=$(tail -20 $GDIR/quality.log 2>/dev/null | grep -cv '"verdict": "OK"' || true)
    log "质量抽检: 最近样本不合格 $BADQ 条 $(tail -3 $GDIR/quality.log 2>/dev/null | grep -v '"OK"' | head -2 | tr '\n' ' ')"
    BUSY_NOW=$(busy_list)
    if echo ",$SKIP_HOSTS," | grep -qv "r5" 2>/dev/null && [ -n "$SKIP_HOSTS" ] && [ -z "$BUSY_NOW" ]; then
      log "临时占用机已释放，补投部署（r5/r13 永久排除不变）"
      SKIP_HOSTS="$BASE_EXCLUDE" python3 $STG/deploy_all.py >> $HUB/guardian.log 2>&1
      SKIP_HOSTS="$BASE_EXCLUDE"
    fi
    if [ $TRIPS -gt 15 ] || [ $DEAD429 -gt 40 ]; then
      global_cool "trip=$TRIPS 429死信=$DEAD429 超阈"
    elif [ -n "$LOWDISK" ]; then
      for hd in $LOWDISK; do hh=${hd%%(*}; log "低磁盘 $hh，单机腾盘"; sq $hh 'pkill -f "[f]leet_curl.py"; cd ~/wk_backfill && setsid nohup python3 -u cos_stock_upload.py --purge --workers 3 > stock2.log 2>&1 < /dev/null &'; done
    elif [ $CURLS -eq 0 ]; then
      ZERO_STREAK=$((ZERO_STREAK + 1))
      PEND=$(python3 - <<'PY'
import glob
n=sum(1 for f in glob.glob('/yzp/zhaozy/yangzepeng/0905/demiwtg/state/curation/image_backfill_full_v1/hub/pending_all/r_r*.jsonl') for _ in open(f))
print(n)
PY
)
      if [ $ZERO_STREAK -ge 3 ]; then
        log "连续 3 轮无 worker（done=$DONECNT pending源=$PEND），判定收尾完成，护航结束。COOLS=$COOLS"
        echo finished > $GDIR/EXITED
        break
      fi
      log "无 worker 存活（第${ZERO_STREAK}轮），pending=$PEND；重新部署（幂等）"
      python3 $STG/deploy_all.py >> $HUB/guardian.log 2>&1
    else
      ZERO_STREAK=0
    fi
    sleep 600
    continue
  fi
done
