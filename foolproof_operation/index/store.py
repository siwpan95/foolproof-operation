"""索引存储层：SQLite 按卷分库（WAL），批量事务落库 + 查询 API。

设计取舍（依据阶段 2 实测：全盘 479 万条记录、枚举 58.1s，**瓶颈已转移到落库**）：

- **落库走批量事务 + 二级索引后建**：先无索引批量插入（``executemany``），
  全部插完再 ``CREATE INDEX``。索引随插入维护会让 250 万行的写入慢数倍；
- **不落冗余的路径列**：只存 ``frn_index/name/parent/is_dir/usn``，
  路径按父链**懒回溯**（每步主键命中），因此目录改名后子树路径自动正确、无需失效重建；
- **按卷分库**：``<index_dir>/<vol>.db``，单卷损坏不影响其它卷，也便于只重建一卷；
- **建库可中断可判定**：``meta.complete`` 只在 ``finish_build()`` 置 1；
  中途退出留下的半成品会被 ``needs_rebuild()`` 识别并要求重建，绝不拿半成品当事实源。

主键仍取 FRN 低 48 位（MFT 记录号），与 ``index.paths.PathResolver`` 口径一致。

**已知取舍**：主键是记录号而非「记录号 + 名称」，因此 NTFS 硬链接的多名称会**折叠为最后写入的那一条**
（本仓用途是找 ``python.exe``/``pyvenv.cfg`` 一类可执行与标记文件，硬链接极少，且折叠只会少列一个别名，
不会产生错误路径）。
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .usn import JournalInfo, MFT_ROOT_INDEX, UsnRecord, normalize_volume

__all__ = ["IndexStore", "NodeRow", "StoreStats", "StoreError", "SCHEMA_VERSION", "name_lookup_sql"]

#: 库结构版本；不匹配即要求重建（结构变更时递增）。
SCHEMA_VERSION = 1
#: 批量插入的分批大小（每批一个事务）。
DEFAULT_BATCH_SIZE = 50_000

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nodes (
        frn_index INTEGER PRIMARY KEY,
        name      TEXT NOT NULL,
        parent    INTEGER NOT NULL,
        is_dir    INTEGER NOT NULL DEFAULT 0,
        usn       INTEGER NOT NULL DEFAULT 0
    )
    """,
)
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_nodes_name_lower ON nodes(name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent)",
)

_INSERT_SQL = "INSERT INTO nodes(frn_index, name, parent, is_dir, usn) VALUES(?, ?, ?, ?, ?)"

_UPSERT_SQL = (
    "INSERT INTO nodes(frn_index, name, parent, is_dir, usn) VALUES(?, ?, ?, ?, ?) "
    "ON CONFLICT(frn_index) DO UPDATE SET name=excluded.name, parent=excluded.parent, "
    "is_dir=excluded.is_dir, usn=excluded.usn"
)


class StoreError(Exception):
    """索引库错误（损坏 / 结构不匹配 / 状态非法）。"""


def name_lookup_sql(count: int) -> str:
    """按名字批量查询的 SQL（``count`` 个名字）。

    **必须写成逐项 ``name = ? COLLATE NOCASE`` 的 OR 形式**：写成
    ``name IN (?, ...) COLLATE NOCASE`` 时 SQLite 无法用上 ``COLLATE NOCASE`` 索引，
    实测退化为全表扫描（G: 卷 251 万行 355ms vs 2.0ms，差 177 倍）。
    该函数公开给测试与审计用：测试直接对本 SQL 取 ``EXPLAIN QUERY PLAN`` 断言不出现 ``SCAN nodes``，
    防止日后被"简化"回 ``IN`` 形式而性能静默劣化。
    """
    if count < 1:
        raise ValueError("count 需 >= 1")
    where = " OR ".join(["name = ? COLLATE NOCASE"] * count)
    return "SELECT frn_index, name, parent, is_dir, usn FROM nodes WHERE " + where


class NodeRow:
    """一条索引行（``__slots__``：单卷数百万行，内存与解包成本都要省）。"""

    __slots__ = ("index", "name", "parent", "is_dir", "usn")

    def __init__(self, index: int, name: str, parent: int, is_dir: int, usn: int = 0) -> None:
        self.index = index
        self.name = name
        self.parent = parent
        self.is_dir = is_dir
        self.usn = usn

    @property
    def is_directory(self) -> bool:
        return bool(self.is_dir)

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> "NodeRow":
        return cls(int(row[0]), str(row[1]), int(row[2]), int(row[3]), int(row[4]))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<NodeRow {0} idx={1} parent={2} dir={3}>".format(
            self.name, self.index, self.parent, bool(self.is_dir)
        )


class StoreStats:
    """索引库规模与状态快照。"""

    __slots__ = (
        "volume", "records", "directories", "complete", "journal_id", "next_usn",
        "built_at", "schema_version", "db_bytes", "path",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    def as_dict(self) -> Dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<StoreStats {0} records={1} complete={2} next_usn={3}>".format(
            self.volume, self.records, self.complete, self.next_usn
        )


class IndexStore:
    """单卷索引库（上下文管理器）。

    用法::

        with IndexStore.open("G:", path) as store:
            store.begin_build()
            store.add_records(vol.enumerate_mft())
            store.finish_build(journal_id=info.journal_id, next_usn=info.next_usn)
            store.find_by_name("python.exe")
            store.resolve_path(12345)
    """

    def __init__(
        self,
        volume: str,
        path: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        connection: Optional[sqlite3.Connection] = None,
        owns_connection: Optional[bool] = None,
    ) -> None:
        self.volume = normalize_volume(volume)
        self.path = str(path)
        self.batch_size = max(1, int(batch_size))
        self._conn = connection
        # 外部传入连接时默认不接管其生命周期；``open()`` 自建连接则显式声明接管。
        self._owns_conn = (connection is None) if owns_connection is None else bool(owns_connection)
        self.dirty = False

    # ── 打开 / 关闭 ───────────────────────────────────

    @classmethod
    def open(
        cls,
        volume: str,
        path: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        memory: bool = False,
    ) -> "IndexStore":
        """打开（必要时创建）索引库；``memory=True`` 用于测试（``:memory:``）。"""
        if not memory:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(":memory:" if memory else path, check_same_thread=False)
        store = cls(
            volume, path, batch_size=batch_size, connection=conn, owns_connection=True
        )
        try:
            store._configure()
            store._ensure_schema()
        except Exception:
            # 打开失败必须回收连接：否则上层（如结构版本不匹配）会静默泄漏句柄
            conn.close()
            raise
        return store

    def _configure(self) -> None:
        """PRAGMA：WAL + 降级同步 + 内存临时表 + 大缓存（落库吞吐的关键）。"""
        assert self._conn is not None
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA cache_size=-65536")  # 64 MB
        self._conn.execute("PRAGMA locking_mode=NORMAL")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        for statement in _DDL:
            self._conn.execute(statement)
        stored = self.get_meta("schema_version")
        if stored is not None and int(stored) != SCHEMA_VERSION:
            raise StoreError(
                "索引库结构版本不匹配（库: {0} / 代码: {1}）：请重建索引".format(stored, SCHEMA_VERSION)
            )
        if stored is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("volume_label", self.volume)

    def close(self) -> None:
        if self._conn is not None and self._owns_conn:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> "IndexStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("索引库已关闭")
        return self._conn

    # ── meta ─────────────────────────────────────────

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row[0]

    def set_meta(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.connection.commit()

    # ── 建库 ─────────────────────────────────────────

    def begin_build(self) -> None:
        """开始全量建库：清空数据、摘除二级索引、标记未完成。"""
        conn = self.connection
        conn.execute("DROP INDEX IF EXISTS idx_nodes_name_lower")
        conn.execute("DROP INDEX IF EXISTS idx_nodes_parent")
        conn.execute("DELETE FROM nodes")
        conn.commit()
        self.set_meta("complete", "0")
        self.set_meta("built_at", "")
        self.set_meta("record_count", "0")
        self.dirty = True

    def add_records(self, records: Iterable[UsnRecord], *, batch_size: Optional[int] = None) -> int:
        """批量插入（分批事务）；返回插入行数。根记录不入表。"""
        conn = self.connection
        size = batch_size or self.batch_size
        buffer: List[Tuple[int, str, int, int, int]] = []
        total = 0
        for record in records:
            if record.index == MFT_ROOT_INDEX or not record.name:
                continue
            buffer.append(
                (
                    record.index,
                    record.name,
                    record.parent_index,
                    1 if record.is_directory else 0,
                    record.usn,
                )
            )
            if len(buffer) >= size:
                self._insert_batch(buffer)
                total += len(buffer)
                buffer = []
        if buffer:
            self._insert_batch(buffer)
            total += len(buffer)
        self.dirty = True
        return total

    def _insert_batch(self, rows: List[Tuple[int, str, int, int, int]], *, upsert: bool = False) -> None:
        """一个事务插一批。建库走**纯 INSERT**（表刚清空，重复只可能来自硬链接多名称），
        遇冲突再退回 upsert —— 纯 INSERT 比 ``ON CONFLICT DO UPDATE`` 明显更快。"""
        conn = self.connection
        if upsert:
            with conn:
                conn.executemany(_UPSERT_SQL, rows)
            return
        try:
            with conn:
                conn.executemany(_INSERT_SQL, rows)
        except sqlite3.IntegrityError:
            with conn:
                conn.executemany(_UPSERT_SQL, rows)

    def finish_build(
        self, *, journal_id: Optional[int] = None, next_usn: Optional[int] = None
    ) -> None:
        """完成建库：建二级索引、写 journal 状态、置 complete=1（唯一的事实源开关）。"""
        conn = self.connection
        started = time.monotonic()
        for statement in _INDEX_DDL:
            conn.execute(statement)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        self.set_meta("record_count", str(count))
        if journal_id is not None:
            self.set_meta("journal_id", str(journal_id))
        if next_usn is not None:
            self.set_meta("next_usn", str(next_usn))
        self.set_meta("built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.set_meta("index_build_seconds", "{0:.2f}".format(time.monotonic() - started))
        self.set_meta("complete", "1")
        self.dirty = False

    def abort_build(self) -> None:
        """放弃本次建库（保留 complete=0，供上层判定重建）。"""
        self.connection.execute("DELETE FROM nodes")
        self.connection.commit()
        self.set_meta("record_count", "0")

    @property
    def interrupted(self) -> bool:
        """是否存在「建了一半」的状态（complete=0 但有库文件）。"""
        return self.get_meta("complete", "0") != "1"

    # ── 增量 ─────────────────────────────────────────

    def apply_changes(self, records: Iterable[UsnRecord]) -> Dict[str, int]:
        """应用 journal 增量（改名/新增 upsert、删除 delete），返回动作计数。

        计数键为 ``upsert`` / ``remove`` / ``ignore``：批量写入**不逐行回查是否已存在**
        （那会让每行多一次查询），因此不区分「新增」与「更新」——
        需要该区分的是内存版 ``paths.PathResolver.apply_changes``（它有全表可判）。
        """
        from .usn import REASON_FILE_DELETE, REASON_RENAME_OLD_NAME, REASON_RENAME_NEW_NAME

        conn = self.connection
        upserts: List[Tuple[int, str, int, int, int]] = []
        deletes: List[int] = []
        counts = {"upsert": 0, "remove": 0, "ignore": 0}
        for record in records:
            if record.index == MFT_ROOT_INDEX:
                counts["ignore"] += 1
                continue
            if record.reason & REASON_FILE_DELETE:
                deletes.append(record.index)
                counts["remove"] += 1
                continue
            if record.reason & REASON_RENAME_OLD_NAME and not (record.reason & REASON_RENAME_NEW_NAME):
                counts["ignore"] += 1
                continue
            if not record.name:
                counts["ignore"] += 1
                continue
            upserts.append(
                (
                    record.index,
                    record.name,
                    record.parent_index,
                    1 if record.is_directory else 0,
                    record.usn,
                )
            )
            counts["upsert"] += 1
        if deletes:
            with conn:
                conn.executemany("DELETE FROM nodes WHERE frn_index = ?", [(i,) for i in deletes])
        if upserts:
            for start in range(0, len(upserts), self.batch_size):
                self._insert_batch(upserts[start : start + self.batch_size], upsert=True)
        self.dirty = True
        return counts

    def set_journal_state(self, *, journal_id: Optional[int] = None, next_usn: Optional[int] = None) -> None:
        """推进增量游标（只在前一次落库成功之后调用）。"""
        if journal_id is not None:
            self.set_meta("journal_id", str(journal_id))
        if next_usn is not None:
            self.set_meta("next_usn", str(next_usn))

    # ── 查询 ─────────────────────────────────────────

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])

    def directory_count(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM nodes WHERE is_dir = 1").fetchone()[0]
        )

    def get_node(self, index: int) -> Optional[NodeRow]:
        row = self.connection.execute(
            "SELECT frn_index, name, parent, is_dir, usn FROM nodes WHERE frn_index = ?", (index,)
        ).fetchone()
        return None if row is None else NodeRow.from_row(row)

    def find_by_name(self, name: str, *, limit: Optional[int] = None) -> List[NodeRow]:
        """按文件名精确查找（大小写不敏感，走 ``COLLATE NOCASE`` 索引）。"""
        sql = "SELECT frn_index, name, parent, is_dir, usn FROM nodes WHERE name = ? COLLATE NOCASE"
        params: List[Any] = [name]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [NodeRow.from_row(row) for row in self.connection.execute(sql, params)]

    def find_by_names(
        self, names: Iterable[str], *, limit_per_name: Optional[int] = None
    ) -> Dict[str, List[NodeRow]]:
        """批量按名查找：一次查询取回全部名字的命中（环境发现的主查询，走 ``name_lookup_sql``）。"""
        wanted = list(dict.fromkeys(names))
        if not wanted:
            return {}
        lowered = {name.lower(): name for name in wanted}
        sql = name_lookup_sql(len(wanted))
        result: Dict[str, List[NodeRow]] = {name: [] for name in wanted}
        for row in self.connection.execute(sql, wanted):
            node = NodeRow.from_row(row)
            key = lowered.get(node.name.lower())
            if key is None:
                continue
            if limit_per_name is not None and len(result[key]) >= limit_per_name:
                continue
            result[key].append(node)
        return result

    def children(self, index: int, *, limit: Optional[int] = None) -> List[NodeRow]:
        """某目录的直接子项（走 ``parent`` 索引）。"""
        sql = "SELECT frn_index, name, parent, is_dir, usn FROM nodes WHERE parent = ?"
        params: List[Any] = [index]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [NodeRow.from_row(row) for row in self.connection.execute(sql, params)]

    def resolve_path(self, index: int, *, max_depth: int = 512) -> Optional[str]:
        """父链懒回溯成绝对路径；父链断裂或成环返回 ``None``。"""
        if index == MFT_ROOT_INDEX:
            return self.volume + "\\"
        parts: List[str] = []
        seen: set = set()
        cursor = index
        depth = 0
        while cursor != MFT_ROOT_INDEX:
            if cursor in seen or depth > max_depth:
                return None
            seen.add(cursor)
            node = self.get_node(cursor)
            if node is None:
                return None
            parts.append(node.name)
            cursor = node.parent
            depth += 1
        parts.reverse()
        return self.volume + "\\" + "\\".join(parts)

    def resolve_paths(self, indices: Iterable[int]) -> Dict[int, Optional[str]]:
        return {index: self.resolve_path(index) for index in indices}

    # ── 状态判定 ─────────────────────────────────────

    def needs_rebuild(self, journal: Optional[JournalInfo]) -> Tuple[bool, str]:
        """是否需要重建：完整标记 / 结构版本 / journal 重建 / 游标回卷，任一命中即重建。"""
        if self.interrupted:
            return True, "上次建库未完成（complete=0）"
        stored_journal = self.get_meta("journal_id")
        stored_next = self.get_meta("next_usn")
        if stored_journal is None or stored_next is None:
            return True, "缺少 journal 状态（非本工具所建）"
        if journal is None:
            return False, ""  # 无 journal：只能靠全量重扫，由上层决定
        if int(stored_journal) != int(journal.journal_id):
            return True, "journal 已重建（ID 变化）"
        if int(journal.first_usn) > int(stored_next):
            return True, "游标落后于 journal 窗口（变更已丢失）"
        if int(stored_next) > int(journal.next_usn):
            return True, "游标超前于 journal（状态异常）"
        return False, ""

    def sync_lag(self, journal: Optional[JournalInfo]) -> Optional[int]:
        """距最新 USN 还差多少条（无 journal 或未建库返回 None）。"""
        if journal is None:
            return None
        stored_next = self.get_meta("next_usn")
        if stored_next is None:
            return None
        return max(0, int(journal.next_usn) - int(stored_next))

    def stats(self) -> StoreStats:
        stored_next = self.get_meta("next_usn")
        stored_journal = self.get_meta("journal_id")
        size = 0
        if self.path != ":memory:" and os.path.isfile(self.path):
            size = os.path.getsize(self.path)
        return StoreStats(
            volume=self.volume,
            records=self.count(),
            directories=self.directory_count(),
            complete=not self.interrupted,
            journal_id=int(stored_journal) if stored_journal else None,
            next_usn=int(stored_next) if stored_next else None,
            built_at=self.get_meta("built_at") or None,
            schema_version=int(self.get_meta("schema_version", "0") or 0),
            db_bytes=size,
            path=self.path,
        )

    def __repr__(self) -> str:
        return "<IndexStore {0} path={1}>".format(self.volume, self.path)
