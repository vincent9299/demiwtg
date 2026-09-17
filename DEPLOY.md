# 舰队部署与拓扑手册（2026-09-17 重构版：湖机=master，25 机公网直连）

> 本版取代三机时代（D3）旧口径，历史见 git。变更决策与完整对照表见
> `HANDOVER_TRAINING_MACHINE.md` §九（接班后变更记录）。

## 一、机器拓扑

**湖机（本训练机）= 唯一 master/指挥位**。25 台腾讯 SG 机全部公网 IP 直连
（湖机出网唯一通路=公司代理 10.127.48.4:3128，经 pconn.py CONNECT 隧道），
**无跳板**。CN 组 pipeline-e~i 已于 2026-09-10 释放。

| 新名 | 旧名 | 公网 | 内网 | 密钥 | 备注 |
|---|---|---|---|---|---|
| p1 | pipeline-a | 43.160.215.28 | 10.3.4.14 | lighthouse_key | 投喂清单 extsdc/extpubchem 所在 |
| p2 | pipeline-b | 43.160.238.29 | 10.3.4.16 | lighthouse_key | |
| p3 | pipeline-c | 43.160.201.131 | 10.3.0.17 | lighthouse_key | 投喂清单 extdf20/extplantnet 所在 |
| p4 | pipeline-d | 43.160.240.239 | 10.3.8.9 | lighthouse_key | 空闲最健康，重活优先 |
| p5 | sg-master | 43.160.250.196 | 10.3.0.14 | cluster_key | **降级普通节点**；审计嗅探+工具箱收割后可退役 |
| r1~r20 | （不变） | 见 `~/.ssh/config` | 10.3.x.x | lighthouse_key | SDC fetch 舰队 |

- 别名全部在湖机 `~/.ssh/config`（含 IP 全表与备份指针
  `config.bak-20260917-jumpera`）。
- **机器间 10.3.x.x 内网互通、22022-22029 反向隧道（湖侧
  `/root/tunnel_keepalive.sh`）、各机 cosfs 认证挂载，均不因直连改造而变。**

## 二、通道纪律（红线）

1. **代理惩罚突发 CONNECT**：对全舰队并发建连会连坐断隧道（8 条反向隧道同
   代理出口）。巡检/发射一律顺序 + sleep 错峰。
2. **数据搬运不走 ssh 直连**（耗各机流量包）：大数据走 COS（双树前缀
   `lhcos-data/`，见 COS_BUCKETS.md）或反向隧道 tar 流（lake_sync 口径）。
3. 湖机无 cosfs；COS 访问用签名 API（凭证在 `/root/lake_pull.py` /
   `/root/cos_get.py`；匿名 API 对湖机 403——白名单只认腾讯侧 IP）。

## 三、各机部署物（存量，接手时已就位）

```
~/pipeline/{demiflow,demiwtg-data}   # p1~p5：代码仓（r 机无 github 通道，rsync 供码）
~/pipeline/venv                       # p1~p5：运行环境
~/lake/meta/*.jsonl                   # 全部机：分片清单（追加型，不放 COS）
~/sdc_fetch/                           # r 机 + p2/p4：SDC 下载器与账本
/tmp/{run_fetch.sh,sdc_fetch_fleet.py} # r 机：运行件（/tmp 会被清，丢则从湖机 _staging/sdc_fetch/ 补）
/etc/passwd-cosfs                      # 各机：cos-reader 只读凭证（600）
/tmp/cos_creds                         # 各机：70字节 sid:key（易失，丢了从 p4 拷）
```

## 四、运维注意（历史教训，仍然有效）

1. **cosfs 最终一致性**：大文件写后需沉降，跨机使用前校验；追加型清单一律本地写。
2. **重跑去重**：各机 `~/lake` 保留时重跑自动去重；blob 内容寻址天然幂等。
3. `pkill -f` 自匹配自杀：模式串出现在自己命令行即自杀——用 `[x]` 字符类或
   脚本文件方式重启（血泪坑 #3，多次复发）。
4. 首次部署新机：`sudo apt install python3.12-venv`；主机密钥变更先
   `ssh-keygen -R` 再 accept-new。
5. r 机之间无互信密钥，文件中转走湖机（master 职责）。
