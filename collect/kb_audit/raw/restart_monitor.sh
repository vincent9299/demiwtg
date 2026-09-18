#!/bin/bash
# 重启值守监控（本脚本命令行不含监控文件名，避免 pkill 自杀）
for p in $(pgrep -f "monitor_lanes"); do kill "$p" 2>/dev/null; done
sleep 1
nohup python3 /home/ubuntu/demi/raw/monitor_lanes.py > /dev/null 2>&1 &
echo "monitor pid $!"
