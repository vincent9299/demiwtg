#!/bin/bash
# dl_hf_models.sh —— 带重试与文件日志的 HF 模型下载器（hf-mirror 308 → huggingface.co，经公司代理）
# 用法: bash dl_hf_models.sh <repo_id> <local_dir> <log_file>
set -u
REPO="$1"
DEST="$2"
LOG="$3"
export http_proxy=http://10.127.48.4:3128
export https_proxy=http://10.127.48.4:3128
unset HTTP_PROXY HTTPS_PROXY
export HF_HUB_DISABLE_XET=1

n=0
until /yzp/zhaozy/yangzepeng/0905/env/bin/hf download "$REPO" --local-dir "$DEST" --max-workers 8 >>"$LOG" 2>&1; do
  n=$((n+1))
  echo "[retry $n $(date '+%F %T')] $REPO" >>"$LOG"
  [ "$n" -ge 200 ] && { echo "[GIVEUP $(date '+%F %T')] $REPO" >>"$LOG"; exit 1; }
  sleep 10
done
echo "[DONE $(date '+%F %T')] $REPO -> $DEST" >>"$LOG"
