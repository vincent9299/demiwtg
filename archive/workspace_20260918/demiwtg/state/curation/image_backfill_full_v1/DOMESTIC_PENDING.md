# 国内源待办（2026-09-17 用户决定：国内先不下载，回头开国内机器再跑）

## 现状

- 国内源候选共 48,513 行（bing_images/toutiao/baidu/huaban_api/quark_images/so360）
- 本机已跑两遍（走公司代理，瞬态跳过率高）：**成功约 6,000+ 已入库**，死信 1,863
  （以 sha_mismatch 为主，即 CDN 内容已变，重试无效）
- ****剩余待下 39,809 行**（2026-09-17 06:00 盘点，本机累计成功约 6,280 张已入库、死信 2,344；dom_00~22.jsonl.gz 已按最新状态重新生成，可直接分发国内机器）**（已扣掉本地已有 blob 与已确认死信），就绪文件：
  `state/curation/image_backfill_full_v1/dom_00.jsonl.gz ~ dom_22.jsonl.gz`（23 份，可直接分发）
- 曾短暂分发到 r 机跑过几分钟（直连国内 CDN 正常，约 5 行/秒），已按用户要求全部停止；
  r 机 `~/wk_backfill/domrun_XX/` 里可能有少量产出，回收 fleet 时一并核对

## 用户将来开国内机器时怎么跑

1. 把 `dom_*.jsonl.gz` 分发到国内机器，每台一份
2. 下载器用 `/yzp/zhaozy/yangzepeng/0905/demiwtg-data/image_backfill/fleet_curl.py`
   （带国内源防盗链头表；用法 `python3 fleet_curl.py --candidates dom_XX.jsonl.gz --out-dir domrun_XX`，
   仅依赖 python3+curl，SHA256 闸门+原子写+done/dead 双清单幂等）
3. 跑完回收：按 done.jsonl 校验 SHA 后导入 `datasets/demiwtg/blobs/`
4. 死信中 sha_mismatch 类是真实损耗（CDN 换图），留给 alternative_urls 定向尝试

## 关键结论（为什么本机跑不好）

本机出网必须走公司代理 10.127.48.4:3128，对国内图源限速极紧：backfill 处理的行中
约 80% 被瞬态耗尽静默跳过（不记死信），多遍重跑收敛极慢。国内直连机器无此问题。
