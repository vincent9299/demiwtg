# GBIF zip 直收路线(2026-09-22 建成即停,工程归档)

**状态:用户拍板停线(2026-09-22 12:26 UTC,"生物图太多")。资产归档供将来重启或移植同类 zip 源。**

## 停线时的进度
- 全局网格 g 片 8/201 入 COS(另有旧 per-slice 网格 m*_p* 53 片)= kb/osm_parts/gbif/ 共 61 片 ~31GB(可整删,待拍板)
- queue-b4-gbif(滴灌)1662 批 idle、0 done(毒行 bug 卡死,见下);queue-b4-gbifdl 未生产
- GBIF download key `0000975-260921141020460`(3.14亿 StillImage / 164.5GB,账号 mengdebin)GBIF 侧通常保留数月

## 核心情报(重启时直接用)
- **成员表**(sgx ~/gbif_members.json,zipcd2.py 拉尾部 1MB 解析,已修 zip64 locator 定位+struct 少一 Q 两个坑):
  occurrence.txt 84.5G @off 6,489,828 / **verbatim.txt 57.3G @84,524,976,623(不需要)** / multimedia.txt 22.6G @141,856,044,617 / CD@164,455,980,902(230KB, 2455 条)
- **稀疏 zip 实证**:`unzip -p` 在全尺寸稀疏文件(挖洞 57.3G)上正常提取——只拉 201 片/102.4GB 即可,省 38%(sparse_test 全过)
- sgx 直连 GBIF 通畅(206 随时可得);r 机代理池在激进拉取后会 429 热约 50 分钟

## 脚件清单
- `px5_pull.sh`:全局 512MB 网格拉片器(COS head 跳过免睡、429 无限梯子 sleep 递增封顶 600s 不换片、每片轮换身份、3 pass 自补洞、片后 sleep 2700±300)
- `assign.json`:201 片 → 20 机分配(IDXSPEC 传参)
- `rollout_one.sh` / `stop_gbif_one.sh`:单机起停(杀链先杀父 bash、模式行首锚定防自匹配)
- `sgx_wait_assemble.py`(等 201 片+稀疏装配)、`sgx_gbif_parse2.py`、`sgx_gbif_chain2.sh`
- sgx 线上另有:~/zipcd.py(旧版,zip64 有 bug 勿用,用湖侧 zipcd2.py)、~/wd_b4_bridge.nt(3,325,204 P846)

## 已修未部署的坑(重启前必看)
1. **原 sgx_gbif_parse.py 两个致命坑**(sgx 线上还是旧版):①produce bid 偏移(每次 flush start_index=0+skip_existing → 只产第一批);②全量 gid2tk dict 会 OOM。都用 sgx_gbif_parse2.py 取代。
2. **b4_op.py _store_image 毒行坑**(湖侧母本已修 2026-09-22,r 机未部署):死图 URL retries_exhausted 旧版归瞬态→炸批死循环;新版仅 429/5xx/408/throttled 保留瞬态,网络耗尽类行级死信。
