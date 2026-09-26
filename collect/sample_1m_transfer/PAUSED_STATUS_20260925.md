# 传输已暂停(2026-09-25 01:2x,用户指示"先暂停")

## 停机现场数字
- SG→GZ 复制:**3,169,607 / 4,683,826**(67.7%),done2.sha 断点,0 失败
- 湖侧落盘:**142,483 张**(~2.2TB 剩余中已到湖的部分;done.sha 断点;fail.sha 1,905 条 NFS 旧瞬时失败,恢复后自动重试)
- 轻资产:20.46GB 已全部上到 GZ relay1m/_assets/(68件,VM-12-15 快照可能缺,重跑上传器即补);湖已落 ~6.5GB(assets/ 目录,size 跳过式续传)
- 全部组件已停:escort.sh、pull_driver、asset_puller、sgx copy_driver、sgx asset_upload
- GZ 桶 relay1m/ 现存 ~4.7TB 中转件(恢复拉取所需,**别删**);SG 桶 relay1m/ 有 ~30万 排障期误拷贝(与恢复无关,可清)

## 恢复方法(任意会话,三条命令)
```
# 1) sgx 侧续复制(断点续):
ssh sgx 'cd ~ && nohup python3 relay1m_copy/copy_driver.py >> relay1m_copy/driver2.log 2>&1 &'
# 2) 湖侧续拉取(断点续,自动追前沿+补 1905 旧失败):
cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer && nohup python3 pull_driver.py >> pull.log 2>&1 &
# 3) 轻资产续拉:
cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer && nohup python3 asset_puller.py >> asset_puller.log 2>&1 &
# (可选)护航自动重启:
cd /yzp/zhaozy/yangzepeng/0905/demiwtg/collect/sample_1m_transfer && nohup ./escort.sh >> escort.log 2>&1 &
```

## 暂停期间的成本提示
- GZ 桶 ~4.7TB 中转件按小时计存储费(约 0.1元/GB/月 → ~470元/月量级,按天折算很小,但长期挂起会累积)
- SG 桶 ~0.45TB 误拷贝垃圾同理(量更小)
- 全部传完并验证后应清理 GZ relay1m/ 与 SG relay1m/(清单见 MORNING_REPORT_20260925.md)
