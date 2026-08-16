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
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_claim "
                    "ON items (status, wave, next_try, rank)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_src_pending "
                    "ON items (status, source, wave)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_instance ON items (instance, status)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_url ON items (content_url, status)")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_uniq "
                    "ON items (wave, instance, content_url)")
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

    def claim(self, since: float = 0.0):
        """原子取一件可下载任务；无则返回 None。

        取件用 BEGIN IMMEDIATE 事务：并发 worker 逐个串行领取，同一行不会被领走两次
        （sqlite 默认延迟事务在 WAL 下读快照可能过期，导致并发重复领取）。

        取件顺序 = 源打散优先：挑「在途 claimed 件数最少」且「在途未达该源动态
        上限（src_caps，AIMD 自适应）」的源，同源内按 wave/rank/id。慢源占着大量
        claimed 或 429/超时把上限砍低时，新取件自动让开去别的源，避免全体 worker
        阻塞在同一类慢源；所有源满额时返回 None（worker 短暂空转，等效 fleet
        级降速），仅剩一个源且未满额时不受影响照常取。

        since 过滤 done 计数：仅统计本轮（updated_at >= since）完成的下载，
        防止同 run-id 重启时旧队列行造成封顶/达标误判。
        """
        now = time.time()
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
        tried = []
        for _ in range(200):
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 源打散 + 自适应额度：先挑「当前在途件数最少」且「在途未达该源
                # 动态上限」的源。慢源 claimed 件多（占着不放）时 worker 自然让开
                # 去取别的源；某源 429/超时多时其上限被 AIMD 砍低，等效全体对该源
                # 降并发；所有源都满额时 worker 短暂空转，等效 fleet 级降速。
                q = ["SELECT p.source FROM ("
                     "SELECT source, MIN(wave) AS mw FROM items "
                     "WHERE status='pending' AND attempts < ? AND next_try <= ? "
                     "GROUP BY source) p "
                     "LEFT JOIN (SELECT source, COUNT(*) AS n FROM items "
                     "WHERE status='claimed' GROUP BY source) f ON f.source = p.source "
                     "LEFT JOIN src_caps c ON c.source = p.source "
                     "WHERE COALESCE(f.n, 0) < COALESCE(c.cap, ?)"]
                params = [MAX_ATTEMPTS, now, DEFAULT_SRC_CAP]
                if tried:
                    q.append(" AND p.source NOT IN (%s)"
                             % ",".join("?" * len(tried)))
                    params.extend(tried)
                q.append(" ORDER BY COALESCE(f.n, 0) ASC, p.mw ASC, p.source ASC "
                         "LIMIT 1")
                row = conn.execute("".join(q), params).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return None
                src = row[0]
                cur = conn.execute(
                    """SELECT id, instance, source, content_url, rank, cap, instance_min, payload
                       FROM items
                       WHERE status='pending' AND attempts < ? AND next_try <= ?
                         AND source=? AND content_url NOT IN
                             (SELECT content_url FROM items WHERE status='claimed')
                       ORDER BY wave, rank, id LIMIT 1""",
                    (MAX_ATTEMPTS, now, src))
                row = cur.fetchone()
                if row is None:
                    # 该源待领件全被同 URL 占走：本轮回退换源
                    tried.append(src)
                    conn.execute("ROLLBACK")
                    continue
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
                if blocked:
                    conn.execute(
                        "UPDATE items SET status='skipped', updated_at=? WHERE id=?",
                        (now, iid))
                    conn.execute("COMMIT")
                    continue
                conn.execute(
                    "UPDATE items SET status='claimed', attempts=attempts+1, "
                    "claimed_at=?, updated_at=? WHERE id=?", (now, now, iid))
                conn.execute("COMMIT")
                return {"id": iid, "instance": instance, "source": source,
                        "content_url": content_url, "rank": rank,
                        "cap": cap, "instance_min": instance_min,
                        "payload": json.loads(payload)}
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return None

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

    def tag_done_count(self, instance: str, since: float = 0.0) -> int:
        with self._conn():
            return self._conn().execute(
                "SELECT COUNT(*) FROM items WHERE instance=? AND status='done' "
                "AND updated_at >= ?", (instance, since)).fetchone()[0]

    def tag_wave_rows(self, instance: str, wave: int) -> dict:
        """返回 {content_url: {"attempts": n, "status": s}}（指定标签某波次）。"""
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
