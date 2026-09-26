#!/bin/bash
# 1M 抽样图传输护航:组件存活检查 + 自动重启 + 状态记录
D=/yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer
S=$D/escort.status
restarts=0
cycle() {
  ts=$(date "+%m-%d %H:%M")
  # 1) lake pull driver
  if ! pgrep -f "python3 pull_driver.py" > /dev/null; then
    if [ $restarts -lt 50 ]; then
      cd $D && nohup python3 pull_driver.py >> pull.log 2>&1 &
      restarts=$((restarts+1))
      echo "$ts RESTART pull_driver (#$restarts)" >> $S
    fi
  fi
  # 2) sgx copy driver
  cdone=$(ssh -o ConnectTimeout=20 sgx 'wc -l < ~/relay1m_copy/done2.sha 2>/dev/null; pgrep -fc "python3 relay1m_copy/copy_driver.py" || true; tail -1 ~/relay1m_copy/driver2.log; pgrep -fc "python3 relay1m_copy/asset_upload.py" || true' 2>/dev/null)
  ccount=$(echo "$cdone" | sed -n 1p)
  calive=$(echo "$cdone" | sed -n 2p)
  clast=$(echo "$cdone" | sed -n 3p)
  aalive=$(echo "$cdone" | sed -n 4p)
  if [ "$calive" = "0" ] && [ "${ccount:-0}" -lt 4683826 ] && [ $restarts -lt 50 ]; then
    ssh -o ConnectTimeout=20 sgx 'cd ~ && nohup python3 relay1m_copy/copy_driver.py >> relay1m_copy/driver2.log 2>&1 & echo restarted' > /dev/null 2>&1
    restarts=$((restarts+1))
    echo "$ts RESTART copy_driver (#$restarts)" >> $S
  fi
  # 3) lake pull stats
  ldone=$(wc -l < $D/done.sha 2>/dev/null || echo 0)
  lfail=$(wc -l < $D/fail.sha 2>/dev/null || echo 0)
  llast=$(grep "\[prog\]" $D/pull.log 2>/dev/null | tail -1)
  echo "$ts copy_done=${ccount:-?} pull_done=$ldone fail=$lfail copy_alive=$calive asset_up=$aalive | $clast | $llast" >> $S
}
while true; do
  cycle
  sleep 600
done
