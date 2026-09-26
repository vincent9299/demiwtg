# 嗅探审计恢复手册（2026-09-17 15:2x 更新：7/8 完成，仅剩 01 片）

> 状态：**00/02/03/04/05/06/07 共 7 片已完成并归档校验**（行数与 sniffshard
> 一致 + SNIFF_DONE + 湖侧签名 HEAD 尺寸相符）。**仅剩 shard 01 未跑**
> （r1 上曾被误停于 ~8 万行，部分输出已废弃——整片重跑即可）。
> 本手册只保证"可在任何机器恢复"，不预启动。

## 恢复 shard 01（任意空闲腾讯 SG 机器）

```bash
# 0) 前置：匿名读 COS（r 机实测通）；/tmp/cos_creds 用于拉输入/传输出
mkdir -p ~/sniff_resume && cd ~/sniff_resume
A=/lhcos-data/demiwtg-data/archive/p1-p5-release-20260917   # cosfs 路径；无 cosfs 用 cos_cat 签名拉
cp $A/raw/cos_sniff.py $A/raw/cos_cat.py $A/raw/stream_cos.py /tmp/
cp $A/sniff_worker.sh /tmp/ && chmod +x /tmp/sniff_worker.sh
cp $A/private/cos_creds.p5 /tmp/cos_creds && chmod 600 /tmp/cos_creds
setsid nohup bash /tmp/sniff_worker.sh 1 > /tmp/sniff_work_launch.out 2>&1 < /dev/null &
```

## 验收与后续

- worker 日志 `/tmp/sniff_work_01.log`：`SNIFF_DONE` → 行数校验（应 990,629）→ `ALL_DONE`
- 参考速率：r2-r4 实测 24 线程 ~424 行/s，单片 **~39-40 分钟**（2328-2404s）
- 产物自动直传 `raw/state/sniffout_01.tsv`（本归档目录）
- 8 片齐后接 join2：`audit_join2.py`（湖 kb_audit/ 或归档 raw/ 有），输入读本
  归档，产物 poison_html_rows / poison_other_rows / audit_final_stats 写本归档 raw/state/

## 已知坑（沿承）

- pkill 自匹配：杀 worker 用 `pkill -f 'sniff_[w]orker'` 括号法
- cos_sniff 输出覆盖写：中断即整片重来，无部分收割价值
- 24 线程/机已验证安全（r2-r4 全片跑通）
