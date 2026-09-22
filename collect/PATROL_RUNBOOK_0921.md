# 夜间护航手册 2026-09-21 22:00 → 09-22 22:00 UTC（每 20 分钟一轮）

> 供定时巡逻会话执行。每轮按"固定巡检"跑一遍,再按"阶段动作"里首个未完成的阶段推进。
> 日志统一追加 `/tmp/patrol_0921.log`(带 UTC 时间戳)。**红线:不动 b2/b3 在跑进程;
> 不删任何 COS 对象;杀进程用精确 PID,禁止 pkill -f(会自杀);b3 1280→1920 升级
> pass 不在夜间自动执行,只记待办。**

## 固定巡检(每轮必做)

1. 快照:`bash /tmp/escort_snap.sh`;再用本机 env python 走 COSQueue snapshot 细分
   si/inat/b3 的 done/inflight/pending(queue 前缀 queue-b2-si / queue-b2-inat / queue-b3;
   creds=image_backfill/.cos_creds,桶 lhcos-368f6-1256345599 ap-singapore,前缀
   lhcos-data/demiwtg-data/)。B4 发射后加看 queue-b4-tmdb / queue-b4-gbif。
2. 速率抽测:任选 3 台 r 机,统计其 run_kb_*/manifest.jsonl 最近 10 分钟 ok 行数
   (fetched_at 时间戳过滤,参考格式见 patrol_0921.log 首轮),外推全网 行/s。
   健康线:b3 ≥ 50 行/s。低于 30 且连续 2 轮 → 按下文"b3 慢"排查。
3. b2 看门狗:`pgrep -f b2_watchdog || (cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect
   && nohup /yzp/zhaozy/yangzepeng/0905/env/bin/python batch2/b2_watchdog.py
   >> /tmp/b2_watchdog.log 2>&1 &)`,重启要记录。
4. si/inat 收口判定:pending+inflight==0 → 记录收官时刻到日志(只记录,不动作)。
5. 一切异常(ssh 失败/COS 报错/未知)记录后继续;单机 ssh 连续 3 轮失败记告警。

## 阶段动作(按序,幂等,靠 /tmp 标记文件去重)

> 顺序 A→B→E 可先行;C 等 b3 收口(阻塞不挡 E);C 完成后转 D 长期护航。

### 阶段 A:桥表落地(标记 /tmp/b4_bridge_done)
> **19:15 起抽取已移到湖机**(sg1 突发型实例 CPU 积分耗尽,bzip2 只有 2.6MB/s;
> 8 分段已上传 COS kb/wd_parts/,湖侧 24 路 ranged 拉取+解压)。
> 桥表落点=**湖侧 /root/wd_work/**:wd_b4_bridge.nt + wd_meta_bridge.nt,
> 状态文件 /root/wd_work/status(EXTRACT_DONE b4=N meta=M)。
- status 无 EXTRACT_DONE 且 /root/wd_parts 在增长 → 正常等待。
- run3.log 有 FAIL 或进程全无且未完成 → 查 /root/wd_work/run3.log,重跑拉取器
  (/root/wd_lake_pull3.py,分段断点可重下)。
- EXTRACT_DONE 后执行:
  1. `cp /root/wd_work/wd_b4_bridge.nt /root/wd_work/wd_meta_bridge.nt /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch4/`
  2. gzip 后传 COS `kb/wikidata_bridges/`(put_multipart)
  3. `touch /tmp/b4_bridge_done`,日志记行数;此后可删 COS kb/wd_parts/ 8 个对象省盘
     (湖侧已验证总字节=43458740693 后再删;sg1 本地 wd_parts 同步清理腾出 OSM 盘位)。

### 阶段 B:切 B4 队列(标记 /tmp/b4_queues_cut;依赖 A)
```
cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch4
/yzp/zhaozy/yangzepeng/0905/env/bin/python cut_b4_queues.py --bridge wd_b4_bridge.nt
```
预期 tmdb ~78 万行(约 390 批)、gbif ~365 万行(约 1800 批)。produce 是幂等的。

### 阶段 C:B4 发射(标记 /tmp/b4_launched;依赖 B + b3 收口)
**条件:queue-b3 pending+inflight==0**(用户令:b3 下载完→启动 b4)。执行:
```
bash /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/batch4/deploy_b4_fleet.sh 20 3 3
```
(部署+每机 3 tmdb + 3 gbif worker;幂等可重跑补漏。)
发射后首 3 轮重点:抽查 2 台机 qw_b4_*.log 有无报错(token/COS/导入),b4 队列
done 是否增长;gbif no_media 死信占比 ~50-65% 属预期(冷门物种),不是故障。
b3 worker 空转不杀(留作升级 pass 用)。

### 阶段 E:元数据 dump 三小件(标记 /tmp/b4_meta_done;依赖 A 完成且 wd_parts 已删)
> B4 共 7 项 = 2 图源 + 5 元数据 dump;桥属性已在 wd_meta_bridge.nt,此处拉 dump 本体。
> 全部下到 sg1 /home/ubuntu/meta_dumps/,完成后 gzip 上传 COS lhcos-data/demiwtg-data/kb/meta_dumps/。
> 下载用分段续传套路可参考 /tmp/wd_extract5.sh(简单文件直接 curl -C - -o 也够)。
1. **GeoNames**:`https://download.geonames.org/export/dump/allCountries.zip`(~1.7GB)
   + `admin2Codes.txt` + `countryInfo.txt`;产物 gz 已压则原样传。
2. **GADM 4.1**:`https://geodata.ucdavis.edu/gadm/gadm4.1/gadm_410.zip`(几 GB;若 URL 变,
   WebFetch geodata.ucdavis.edu/gadm.html 找当前全球包链接)。
3. **Getty LOD**:ULAN/AAT/TGN full zip——WebFetch `https://vocab.getty.edu/` 或
   getty.edu/research/tools/vocabularies 找 dataset 当前直链(老模式
   vocab.getty.edu/dataset/{aat,ulan,tgn}/full/*.zip;逐个拉,每个几百 MB)。
上传完成后 `touch /tmp/b4_meta_done`,日志记各件大小。
**OSM(阶段 F,已在跑——湖侧 /root/osm_extract_loop.sh,nohup,日志 /tmp/osm_extract.log,
巡逻只监控不重启)**:流程 = sg1 拉分洲 pbf → 湖侧 osmium 过滤 wikidata tag → 产物
put_multipart 传 COS kb/meta_dumps/osm/<洲>-wikidata.osm.pbf → 双端删原 pbf。
自带 EXTRACT_DONE 守卫(桥表优先)。完成标记 $WORK/<洲>.osm.pbf.done。**WLM(无人工门,阶段 E 顺带)**:对齐边 = meta 桥 P1435/P373
直接出(桥落地即有);monuments db 公开 API(web 界面,仅 UA 礼貌)作可选增量,拿不全
就换公开路径,不设确认关卡。

### 阶段 D:B4 护航(依赖 C)
按固定巡检加看 b4 两队列速率与死信;单机 worker 全挂 → 重跑 deploy 脚本补。
TMDB 队列预计几小时收口;GBIF 大队列跑数天,夜间只需确认在推进。

## 排障手册

- **b3 慢(<30 行/s 连续 2 轮)**:抽 3 台机看 rate_state.json(rps 是否被 AIMD 压低)、
  近 10 分钟 miss 分布(curl:5=代理 DNS 挂了→该机 worker 属于哪个代理组,记录待办,
  夜间不换代理);http:400 突增 → 记录,继续。
- **sg1 磁盘**(桥表阶段):df -h /home/ubuntu,>85% 告警(分段+解压产物 <45G,正常够)。
- **COS 上传失败**:put_bytes 偶发重试即可;持续失败记告警。
- **本机(lake)重启**:/tmp 标记丢失 → 用日志 /tmp/patrol_0921.log 判断阶段重做
  (各阶段幂等)。

## 晨报(09-22 08:00 UTC 附近的那轮额外做)
汇总:si/inat/b3 收官时刻与死信统计;b3 升级 pass 待办(清 manifest 1280 行重下
~25 万行——需白天人工确认方案);B4 两队列进度与预计剩余;四源并账待办清单。
