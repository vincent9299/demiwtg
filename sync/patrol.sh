#!/bin/bash
# 冷备链巡逻：三机进程/盘位/tar 流转 + GZ 心跳 + 队列水位。判活看组合信号，勿单看日志静默。
PATROL_LOG="${PATROL_LOG:-/yzp/zhaozy/yangzepeng/0905/pan123/patrol.log}"
ts() { date -u '+%m-%d %H:%M:%S'; }
{
echo "===== 巡逻 $(ts) UTC ====="
for h in sg1 sg2; do
  echo "--- $h ---"
  ssh -o ConnectTimeout=15 $h '
    pgrep -c -f "cos_relay_[p]ush" | xargs echo "push进程:";
    df -h / | awk "NR==2{print \"盘:\" \$3 \" used/\" \$2 \", \" \$5}";
    du -sh ~/pan123-relay/spool 2>/dev/null | awk "{print \"spool:\" \$1}";
    find ~/pan123-relay/spool/blobs -name "*.tar" 2>/dev/null | wc -l | xargs echo "在库tar:";
    find ~/pan123-relay/spool -newermt "-6 minutes" -type f 2>/dev/null | wc -l | xargs echo "6分钟内有写入文件数:";
    grep -cE "Traceback|CRITICAL|失败" ~/pan123-relay/supervisor.log 2>/dev/null | xargs echo "异常行:";
    grep -c "拉起" ~/pan123-relay/supervisor.log 2>/dev/null | xargs echo "拉起次数:";
    wc -l ~/pan123-relay/spool/dead_units.jsonl 2>/dev/null | xargs echo "sg死信行:";
    grep -E "停推|闸门|水位" ~/pan123-relay/supervisor.log 2>/dev/null | tail -1;
    tail -1 ~/pan123-relay/supervisor.log' 2>&1 | head -10
done
echo "--- cn1 + GZ心跳 ---"
ssh -o ConnectTimeout=15 cn1 '
  pgrep -c -f "python3 cos123_[r]elay" | xargs echo "consume进程:";
  df -h / | awk "NR==2{print \"盘:\" \$3 \" used/\" \$2 \", \" \$5}";
  du -sh ~/pan123-relay/spool 2>/dev/null | awk "{print \"spool:\" \$1}";
  cd ~/pan123-relay && python3 -c "
import cos123_relay as m
st, _, body = m.cos_call(\"GET\", m.STATUS_KEY, timeout=(15,60))
import json, time
d = json.loads(body) if st == 200 else {}
age = int(time.time() - d.get(\"ts\", 0)) if d else -1
print(\"心跳: age=%ss backlog=%.2fGB host=%s\" % (age, d.get(\"backlog_bytes\",0)/1e9, d.get(\"host\",\"?\")))" 2>&1;
  grep -cE "Traceback|CRITICAL" ~/pan123-relay/supervisor.log 2>/dev/null | xargs echo "异常行:";
  grep -c "拉起" ~/pan123-relay/supervisor.log 2>/dev/null | xargs echo "拉起次数:";
  grep -c "] 消费 " ~/pan123-relay/supervisor.log 2>/dev/null | xargs echo "累计消费单元:";
  grep -cE "part-[0-9]+\.tar \(" ~/pan123-relay/supervisor.log 2>/dev/null | xargs echo "已消费tar卷:";
  grep "] 消费 " ~/pan123-relay/supervisor.log 2>/dev/null | tail -1;
  grep -iE "dead|死信" ~/pan123-relay/supervisor.log 2>/dev/null | tail -1' 2>&1 | head -12
} | tee -a "$PATROL_LOG"
