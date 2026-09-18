#!/bin/bash
# 出货收敛环：跑 ship_parts -> SUM 校验 -> 不过就再跑，最多 6 轮；成功自动删本地 zip
for r in 1 2 3 4 5 6; do
  OUT=$(bash /home/ubuntu/demi/raw/ship_parts.sh 2>&1 | tail -3)
  echo "[$(date +%T)] round $r:"; echo "$OUT"
  echo "$OUT" | grep -q "SHIPPED_ALL_PARTS" && { echo CONVERGED; exit 0; }
  sleep 20
done
echo STILL_INCOMPLETE_after_6
