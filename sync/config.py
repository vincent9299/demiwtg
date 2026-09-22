"""demiwtg 冷备链策略单点（所有业务口径集中于此，改口径只动这个文件）。"""
from __future__ import annotations

import hashlib

# ---- 桶/前缀 ----
SRC_HOST_FULL = "lhcos-368f6-1256345599.cos.ap-singapore.myqcloud.com"
DST_HOST_FULL = "lhcos-cee54-1256345599.cos.ap-guangzhou.myqcloud.com"   # 队列桶(公网)
DST_INTERNAL_HOST = "lhcos-cee54-1256345599.cos-internal.ap-guangzhou.myqcloud.com"  # cn1 内网免费
SRC_ROOT = "lhcos-data/demiwtg-data"                     # 源前缀(只读)
QUEUE_ROOT = "pan123-relay"                              # 队列前缀
META_PREFIX = f"{QUEUE_ROOT}/_meta/"
STATUS_KEY = f"{QUEUE_ROOT}/_status/cn1.json"

# ---- 闭集（镜像范围；孤儿 blob 不在内） ----
SUBTREES = [
    "datasets/demiwtg/kb",          # blobs/主账本/概念层/batch2/qid_images_ext/sdc_fetch/pages语料
    "datasets/raw",                 # df20/inat/plantnet300k/pubchem/metmuseum/openimages/smithsonian
    "datasets/candidate",
    "docs",
]
BLOBS_MARK = "datasets/demiwtg/kb/blobs/"
BLOBS_UNIT_PREFIX = "datasets/demiwtg/kb/blobs"          # tar 卷在队列里的前缀

# ---- 毒闸门：blobs 只收 last_modified ≥ 此日期（毒 blob 钉死 09-12~14 窗口） ----
BLOBS_T0 = "2026-09-17"

# ---- 分片：blobs 按内容寻址首段 sha2 取模，其余按 key md5 取模 ----
SHARDS = 2


def shard_of(src_key: str) -> int:
    if src_key.startswith(SRC_ROOT + "/" + BLOBS_MARK):
        sha2 = src_key[len(SRC_ROOT) + 1 + len(BLOBS_MARK):].split("/", 1)[0]
        try:
            return int(sha2, 16) % SHARDS
        except ValueError:
            pass
    return int(hashlib.md5(src_key.encode()).hexdigest(), 16) % SHARDS


def in_closed_set(src_key: str) -> bool:
    rel = src_key[len(SRC_ROOT) + 1:] if src_key.startswith(SRC_ROOT + "/") else ""
    return any(rel == s or rel.startswith(s + "/") for s in SUBTREES)


def blobs_gate_ok(last_modified: str) -> bool:
    return (last_modified or "") >= BLOBS_T0


def queue_unit(src_key: str) -> str:
    """源 key → 队列单元 key（镜像原目录结构）。"""
    return f"{QUEUE_ROOT}/{src_key[len(SRC_ROOT) + 1:]}"


# ---- 镜像布局（cn1 → 123pan） ----
PAN_ROOT = "demiwtg-data"
PAN_MAX_FILE = 9_800_000_000          # 123pan 单文件硬限 10.00GB, 留安全边
READBACK_PCT = 2                      # 读回抽样比例
PAN123_BAD_IPS = {"123.184.218.110"}  # 上传后端毒 IP(2026-09-21 实测; 巡逻复测可更新)


def pan_relpath(unit_rel: str) -> str:
    """队列单元 rel → 123pan 镜像相对路径（原目录结构）。"""
    return unit_rel


# ---- 预算/背压阈值 ----
BUDGET_GB = 35                        # sg 机 spool 盘位预算
BACKLOG_MAX_GB = 50                   # GZ 积压停推线
STALE_MAX_H = 24                      # cn1 心跳超龄停推线
TAR_CAP_GB = 4                        # tar 卷上限(简单 PUT 线内)
MEMBER_CAP_MB = 96                    # tar 成员入内存上限, 超过转 standalone
TAIL_MIN_MEMBERS = 10                 # 停机尾卷成员数下限, 微量弃(下轮重收)
BLOB_FAIL_DEAD = 5                    # blob 下载失败此次数标死(防每圈重查)
LINGER_MIN = 10                       # 半卷 linger 封卷分钟
MULTIPART_TH_MB = 256                 # 大于此走分片(首夜实测口径)
PART_SIZE_MB = 256                    # 分片片大小
WATCHDOG_UNIT_S = 1800                # 单元看门狗 deadline
