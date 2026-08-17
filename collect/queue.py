"""共享下载队列（SQLite 实现，多进程安全）。

供 --queue 队列模式使用：各分片进程把「通过阶段二筛选的候选」投递到共享队列，
随后所有进程以 worker 身份从队列取件下载。要点：
- 原子取件（BEGIN IMMEDIATE 事务内完成状态迁移）：同一 content_url 不会被两个
  worker 同时下载（取件时排除 status='claimed' 的同 URL 行）；
- 源打散取件：优先取「在途 claimed 件数最少」的源，慢源拥堵时 worker 让开取
  其它源，避免全体 worker 卡在同一类慢源；
- 按源自适应在途上限（AIMD）：成功 +1 试探、429/5xx/超时减半降温，
  claim 跳过在途已达上限的源，等效动态调整对该源的并发；
- 失败重试：每个候选最多尝试 MAX_ATTEMPTS 次，仍失败永久跳过（status='skipped'）；
- 掉线兜底：claimed 超过 STALE_CLAIM_SEC 未完成（进程被杀）自动回到 pending；
- 封顶控制：取件时按 (instance,source) 已下载数与 instance 已下载数实时判定封顶，
  封顶行直接跳过，不占用 worker 下载时间；
- since 过滤：done 计数可限定 updated_at >= since，避免「同 run-id 重启复用旧队列」
  时旧行造成封顶/达标误判（旧行对应的图已在主清单，属 existing 而非本轮成果）。
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time

MAX_ATTEMPTS = 3
STALE_CLAIM_SEC = 600.0
RETRY_BASE_SEC = 5.0

# 按源自适应在途上限（AIMD 拥塞控制）：成功 +1 试探、拥堵减半降温。
DEFAULT_SRC_CAP = 4.0
SRC_CAP_MAX = 32.0
SRC_CAP_MIN = 1.0

# 拥堵信号：这类失败意味着「该源此刻承压」，乘性降低该源在途上限。
CONGESTION_FAIL_KINDS = {"timeout", "network_error"}
CONGESTION_HTTP_CODES = {"429", "500", "502", "503", "504"}


def is_congestion_fail(fail_kind: str | None) -> bool:
    """判断失败是否属于拥堵信号（429/5xx/超时/网络错误）。"""
    if not fail_kind:
        return False
    if fail_kind in CONGESTION_FAIL_KINDS:
        return True
    if fail_kind.startswith("http_"):
        return fail_kind[5:] in CONGESTION_HTTP_CODES
    return False


class DownloadQueue:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self._heal_lock = threading.Lock()   # 自愈限频（进程级）
        self._last_heal = 0.0
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        wave INTEGER NOT NULL DEFAULT 1,
                        instance TEXT NOT NULL,
                        source TEXT NOT NULL,
                        content_url TEXT NOT NULL,
                        rank INTEGER NOT NULL DEFAULT 0,
                        cap INTEGER,
                        instance_min INTEGER,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending',
                        payload TEXT NOT NULL,
                        rec TEXT,
                        next_try REAL NOT NULL DEFAULT 0,
                        claimed_at REAL NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL DEFAULT 0
                    )""")
                # 取件核心索引（Q1 逐源枚举 + Q2 单源选件共用，覆盖
                # (status,source) 前缀与 ORDER BY wave,rank,id）：
                # 旧版 Q1 对 66 万 pending 行做 GROUP BY 全扫（1.5s 起，磁盘
                # 饱和下恶化到 45s+ 拖垮全局）。现 Q1 递归 CTE 沿本索引逐源
                # seek 枚举（每源一跳，亚毫秒），Q2 沿本索引序直出免排序。
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_pick "
                    "ON items (status, source, wave, rank, id)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_instance ON items (instance, status)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_url ON items (content_url, status)")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_uniq "
                    "ON items (wave, instance, content_url)")
                # 自愈专用：(status='pending' AND attempts>=3) 直接定位到
                # ('pending',3) 小区间，避免扫 66 万 pending 行持写锁。
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_heal "
                    "ON items (status, attempts)")
                # 废索引清理：claim/src_pending/srcwave 均被 idx_items_pick
                # 的前缀覆盖，留着只会误导 planner（Q2 曾被 srcwave 带偏成
                # 「过滤后临时 B 树全量排序」，单源选件 650ms+）。
                for dead in ("idx_items_claim", "idx_items_src_pending",
                             "idx_items_srcwave"):
                    conn.execute(f"DROP INDEX IF EXISTS {dead}")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS src_caps (
                        source TEXT PRIMARY KEY,
                        cap REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )""")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=120000")
        # checkpoint 粒度 ~8M：下载高峰磁盘 IO 饱和时，提交方内联 checkpoint
        # 会持写锁拷 WAL→主库。粒度越大持锁越久（400M 时实测 100s+），
        # 小粒度多次 checkpoint 总 IO 不变但单次只卡亚秒级。
        conn.execute("PRAGMA wal_autocheckpoint=2000")
        return conn

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _exec_retry(self, fn, tries: int = 8, sleep: float = 1.0):
        """带重试执行单条写入：128 线程高并发下 busy_timeout 耗尽抛
        'database is locked' 时退避重试（写事务本身毫秒级，重试成本低）。"""
        last = None
        for i in range(tries):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e):
                    raise
                last = e
                time.sleep(sleep * (i + 1))
        raise last

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def enqueue(self, wave: int, instance: str, source: str, content_url: str,
                rank: int, cap, instance_min, payload: dict) -> None:
        now = time.time()

        def _do():
            with self._conn():
                self._conn().execute(
                    "INSERT OR IGNORE INTO items "
                    "(wave, instance, source, content_url, rank, cap, instance_min, payload, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (wave, instance, source, content_url, rank, cap, instance_min,
                     json.dumps(payload, ensure_ascii=False), now))
        self._exec_retry(_do)

    def enqueue_many(self, rows: list) -> None:
        """批量投递：一个写事务装多行（rows 元素同 enqueue 参数序）。
        百万级候选逐条 enqueue = 逐条抢写锁，投递期拖垮下载 worker。"""
        if not rows:
            return
        now = time.time()
        data = [(w, i, s, u, rk, cap, im,
                 json.dumps(p, ensure_ascii=False), now)
                for (w, i, s, u, rk, cap, im, p) in rows]

        def _do():
            with self._conn():
                self._conn().executemany(
                    "INSERT OR IGNORE INTO items "
                    "(wave, instance, source, content_url, rank, cap, instance_min, payload, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", data)
        self._exec_retry(_do)

    def claim(self, since: float = 0.0):
        """取一件（兼容入口；内部走批量 claim）。"""
        items = self.claim_many(since, limit=1)
        return items[0] if items else None

    def claim_many(self, since: float = 0.0, limit: int = 8) -> list:
        """批量领取至多 limit 件，返回 item dict 列表（可为空）。

        为什么要批量：单件领取时 Q1/Q2 逐批重跑、且 ORDER BY ... LIMIT 1
        让所有 worker 抢每个源的同一行（羊群效应），乐观锁下绝大多数白干。
        批量领取把选件成本摊薄 limit 倍，并用随机 OFFSET 打散行选择。
        （历史教训：旧版 Q1 全扫 66 万 pending 行，单次 ~1.5s，已改为
        逐源索引探测，亚毫秒级。）

        读查询走 WAL 读路径（不阻塞写），整批 flip 合入一个短写事务；
        源打散 + AIMD 上限（src_caps）语义不变：慢源占额多时自动让开。

        since 过滤 done 计数：仅统计本轮（updated_at >= since）完成的下载，
        防止同 run-id 重启时旧队列行造成封顶/达标误判。
        """
        now = time.time()
        # 自愈（throttled，进程级）：5 分钟才跑一次，且同进程只有一个线程跑。
        # 旧版每次 claim、每个线程都跑：第一条 UPDATE 要扫 66 万 pending 行，
        # 持写锁数秒～数十秒，百级 worker 全在锁后排队，是 claim 等锁
        # 10~140s 的直接推手。自愈只兼容「进程在 claim 与 release 之间被杀」
        # 的罕见残留，低频完全够用。
        with self._heal_lock:
            due = now - self._last_heal > 300.0
            if due:
                self._last_heal = now
        if due:
            with self._conn():
                # 自愈：pending 但 attempts 已满的行永久跳过（防卡死 wave 排空判定）
                self._conn().execute(
                    "UPDATE items SET status='skipped', updated_at=? "
                    "WHERE status='pending' AND attempts >= ?", (now, MAX_ATTEMPTS))
                # 掉线兜底：claimed 超时未完成 → 回到 pending（attempts 保留）；
                # attempts 已满的（第 3 次领取后进程被杀）直接跳过，否则卡死排空。
                self._conn().execute(
                    "UPDATE items SET status=CASE WHEN attempts >= ? THEN 'skipped' "
                    "ELSE 'pending' END, claimed_at=0, updated_at=? "
                    "WHERE status='claimed' AND claimed_at < ?",
                    (MAX_ATTEMPTS, now, now - STALE_CLAIM_SEC))
        return self._exec_retry(lambda: self._claim_many_once(since, now, limit))

    def _claim_many_once(self, since: float, now: float, limit: int) -> list:
        conn = self._conn()
        src_pool = getattr(self._local, "src_pool", None)  # (ts, [sources])
        if src_pool is None or now - src_pool[0] > 20.0:
            # 源打散 + 自适应额度：挑「在途件数最少」且「在途未达该源动态
            # 上限」的源（最多 8 个轮换）。慢源 claimed 多时自然排后；某源
            # 429/超时多时其上限被 AIMD 砍低；所有源满额时空转降速。
            #
            # Q1 = 逐源索引探测（替代旧版 66 万行 GROUP BY 全扫）：
            # 递归 CTE 沿 idx_items_pick 的 (status='pending') 区间按 source
            # 序逐源 seek（每源一跳，COALESCE 哨兵收尾），源数 ~10 时全程
            # 亚毫秒；随后逐源索引计数 claimed 件数、LEFT JOIN src_caps
            # 过滤超额源。attempts/next_try 谓词不进枚举（会使 MIN 优化失效
            # 退化成范围扫描），候选资格由 Q2 逐源判定时兜底。
            q = ["WITH RECURSIVE t(src) AS ("
                 "SELECT COALESCE((SELECT source FROM items INDEXED BY idx_items_pick "
                 "WHERE status='pending' ORDER BY source LIMIT 1), char(1114111)) "
                 "UNION ALL "
                 "SELECT COALESCE((SELECT source FROM items INDEXED BY idx_items_pick "
                 "WHERE status='pending' AND source > t.src "
                 "ORDER BY source LIMIT 1), char(1114111)) "
                 "FROM t WHERE t.src < char(1114111)) "
                 "SELECT source, n FROM ("
                 "SELECT t.src AS source, "
                 "(SELECT COUNT(*) FROM items i2 "
                 "WHERE i2.status='claimed' AND i2.source = t.src) AS n, "
                 "COALESCE(c.cap, ?) AS cap "
                 "FROM t LEFT JOIN src_caps c ON c.source = t.src "
                 "WHERE t.src < char(1114111)) "
                 "WHERE n < cap "
                 "ORDER BY n ASC, source ASC LIMIT 8"]
            rows = conn.execute("".join(q), (DEFAULT_SRC_CAP,)).fetchall()
            src_pool = (now, [r[0] for r in rows])
            self._local.src_pool = src_pool

        out = []
        for src in src_pool[1]:
            if len(out) >= limit:
                break
            need = limit - len(out)
            # 随机 OFFSET 打散：多 worker 不再全抢每源第一行（羊群效应）；
            # 多抓 3 行作竞争冗余，乐观锁输了的行自然丢弃。
            # Q2 钉死 idx_items_pick：索引序即 (wave,rank,id) 序，免临时
            # B 树排序；attempts/next_try 为残余过滤，LIMIT 后即止。
            rows = conn.execute(
                """SELECT id, instance, source, content_url, rank, cap, instance_min, payload
                   FROM items INDEXED BY idx_items_pick
                   WHERE status='pending' AND source=?
                     AND attempts < ? AND next_try <= ?
                     AND content_url NOT IN
                         (SELECT content_url FROM items WHERE status='claimed')
                   ORDER BY wave, rank, id LIMIT ? OFFSET ?""",
                (src, MAX_ATTEMPTS, now, need + 3,
                 random.randint(0, 32))).fetchall()
            picked = []   # [(iid, instance, source, url, rank, cap, imin, payload, blocked)]
            for row in rows:
                iid, instance, source, content_url, rank, cap, instance_min, payload = row
                blocked = False
                if cap and cap > 0:
                    done = conn.execute(
                        "SELECT COUNT(*) FROM items WHERE instance=? AND source=? "
                        "AND status='done' AND updated_at >= ?",
                        (instance, source, since)).fetchone()[0]
                    blocked = done >= cap
                if not blocked and instance_min and instance_min > 0:
                    done = conn.execute(
                        "SELECT COUNT(*) FROM items WHERE instance=? AND status='done' "
                        "AND updated_at >= ?", (instance, since)).fetchone()[0]
                    blocked = done >= instance_min
                picked.append((iid, instance, source, content_url, rank,
                               cap, instance_min, payload, blocked))
                if not blocked and sum(1 for p in picked if not p[8]) >= need:
                    break
            if not picked:
                continue

            flipped = []

            def _do():
                # 整批一个短写事务：逐行乐观锁 flip，rowcount=0 即被抢先。
                # 事务内异常自动回滚，重试时从头再来（幂等）。
                flipped.clear()
                with self._conn():
                    for (iid, instance, source, content_url, rank,
                         cap, instance_min, payload, blocked) in picked:
                        cur = self._conn().execute(
                            "UPDATE items SET status=?, attempts=attempts+1, "
                            "claimed_at=?, updated_at=? WHERE id=? AND status='pending'",
                            ("skipped" if blocked else "claimed", now, now, iid))
                        if cur.rowcount and not blocked:
                            flipped.append({
                                "id": iid, "instance": instance, "source": source,
                                "content_url": content_url, "rank": rank,
                                "cap": cap, "instance_min": instance_min,
                                "payload": json.loads(payload)})

            self._exec_retry(_do)
            out.extend(flipped)
        return out

    def mark_done(self, item_id: int, rec: dict) -> None:
        now = time.time()

        def _do():
            with self._conn():
                self._conn().execute(
                    "UPDATE items SET status='done', rec=?, updated_at=? WHERE id=?",
                    (json.dumps(rec, ensure_ascii=False), now, item_id))
        self._exec_retry(_do)

    def bump_cap(self, source: str, up: bool) -> None:
        """按源自适应在途上限（AIMD）：成功 +1、拥堵减半，全舰共享。

        写入 src_caps 表，所有分片进程的 claim() 实时读取生效；
        无记录时从 DEFAULT_SRC_CAP 起步。
        """
        now = time.time()

        def _do():
            with self._conn():
                row = self._conn().execute(
                    "SELECT cap FROM src_caps WHERE source=?", (source,)).fetchone()
                cur = row[0] if row else DEFAULT_SRC_CAP
                new = min(cur + 1.0, SRC_CAP_MAX) if up else max(cur * 0.5, SRC_CAP_MIN)
                self._conn().execute(
                    "INSERT INTO src_caps (source, cap, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(source) DO UPDATE SET cap=excluded.cap, "
                    "updated_at=excluded.updated_at",
                    (source, new, now))
        self._exec_retry(_do)

    def src_caps(self) -> dict:
        with self._conn():
            rows = self._conn().execute(
                "SELECT source, cap, updated_at FROM src_caps").fetchall()
        return {s: {"cap": c, "updated_at": t} for s, c, t in rows}

    def mark_skipped(self, item_id: int) -> None:
        def _do():
            with self._conn():
                self._conn().execute(
                    "UPDATE items SET status='skipped', updated_at=? WHERE id=?",
                    (time.time(), item_id))
        self._exec_retry(_do)

    def release(self, item_id: int) -> None:
        """失败放回：尝试 < MAX_ATTEMPTS 次回 pending（带退避），否则永久跳过。"""
        now = time.time()

        def _do():
            with self._conn():
                row = self._conn().execute(
                    "SELECT attempts FROM items WHERE id=?", (item_id,)).fetchone()
                if row is None:
                    return
                attempts = row[0] or 0
                if attempts >= MAX_ATTEMPTS:
                    self._conn().execute(
                        "UPDATE items SET status='skipped', updated_at=? WHERE id=?",
                        (now, item_id))
                else:
                    backoff = min(RETRY_BASE_SEC * (2 ** (attempts - 1)), 120.0)
                    self._conn().execute(
                        "UPDATE items SET status='pending', claimed_at=0, next_try=?, "
                        "updated_at=? WHERE id=?", (now + backoff, now, item_id))
        self._exec_retry(_do)

    def reuse_rec(self, content_url: str):
        """同 content_url 已成功下载过 → 返回其清单记录（复用，不再联网）。"""
        with self._conn():
            row = self._conn().execute(
                "SELECT rec FROM items WHERE content_url=? AND status='done' "
                "AND rec IS NOT NULL LIMIT 1", (content_url,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None

    def instance_done_count(self, instance: str, since: float = 0.0) -> int:
        with self._conn():
            return self._conn().execute(
                "SELECT COUNT(*) FROM items WHERE instance=? AND status='done' "
                "AND updated_at >= ?", (instance, since)).fetchone()[0]

    def instance_wave_rows(self, instance: str, wave: int) -> dict:
        """返回 {content_url: {"attempts": n, "status": s}}（指定实例某波次）。"""
        with self._conn():
            rows = self._conn().execute(
                "SELECT content_url, attempts, status FROM items "
                "WHERE instance=? AND wave=?", (instance, wave)).fetchall()
        return {u: {"attempts": a, "status": s} for u, a, s in rows}

    def wave_source_stats(self, wave: int) -> dict:
        """按来源汇总某波次：{source: {total, done, exhausted}}。

        exhausted = 试满 MAX_ATTEMPTS 仍失败的件数（死链/防盗链/超时收敛），
        用于识别「本 run 里该源整体无产出」的弱源。
        """
        with self._conn():
            rows = self._conn().execute(
                """SELECT source,
                          COUNT(*) AS total,
                          SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                          SUM(CASE WHEN status='skipped' AND attempts >= ?
                                   THEN 1 ELSE 0 END) AS exhausted
                   FROM items WHERE wave=? GROUP BY source""",
                (MAX_ATTEMPTS, wave)).fetchall()
        return {s: {"total": t, "done": d or 0, "exhausted": e or 0}
                for s, t, d, e in rows}

    def skip_instance_pending(self, instance: str) -> None:
        with self._conn():
            self._conn().execute(
                "UPDATE items SET status='skipped', updated_at=? "
                "WHERE instance=? AND status='pending'", (time.time(), instance))

    def wave_drained(self, wave: int) -> bool:
        """该波次没有 pending/claimed 项即视为排空（含封顶/失败重试收敛）。"""
        with self._conn():
            row = self._conn().execute(
                "SELECT COUNT(*) FROM items WHERE wave=? AND status IN "
                "('pending','claimed')", (wave,)).fetchone()
            return row[0] == 0

    def drained(self) -> bool:
        with self._conn():
            row = self._conn().execute(
                "SELECT COUNT(*) FROM items WHERE status IN ('pending','claimed')"
            ).fetchone()
            return row[0] == 0

    def counts(self) -> dict:
        with self._conn():
            rows = self._conn().execute(
                "SELECT wave, status, COUNT(*) FROM items GROUP BY wave, status"
            ).fetchall()
        out = {}
        for w, s, n in rows:
            out.setdefault(w, {})[s] = n
        return out
