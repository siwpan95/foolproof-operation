"""索引服务：后台建库 + 增量同步 + 查询（可作为插件行装配）。

三件事：

1. **建库**：``ensure_index(volume)`` —— 判定「缺库 / 半成品 / journal 重建 / 游标回卷」后，
   在后台线程里全量枚举并落库（``store.begin_build`` → ``add_records`` → ``finish_build``）；
2. **增量**：``sync(volume)`` —— 从库里记的 ``next_usn`` 起读 journal，批量 upsert/delete 后推进游标；
3. **查询**：``search(names)`` / ``find(name)`` —— 名字命中 + 父链懒回溯成绝对路径。

**降级纪律**（本项目红线）：卷打不开（``VolumeAccessError``）时**不删索引、不清库**，
只把该卷状态标成 ``denied`` 并在 ``status()`` 里明示；已有索引仍可查询。
**绝不静默**：缺索引的卷在查询结果里以 ``missing`` 显式列出，而不是安静地返回空。

**线程模型**：单后台工作者线程处理任务队列；每个卷一个 SQLite 连接（``check_same_thread=False``）
+ 每卷一把锁；WAL 让读写并发不互相阻塞。
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..core.context import Service

from .store import DEFAULT_BATCH_SIZE, IndexStore, StoreError
from .usn import (
    JournalNotActiveError,
    UsnError,
    UsnVolume,
    VolumeAccessError,
    list_ntfs_volumes,
    normalize_volume,
)

__all__ = ["IndexService", "default_index_dir", "STATUS_*"]

#: 卷状态取值。
STATUS_MISSING = "missing"      # 还没有索引
STATUS_BUILDING = "building"    # 正在建库
STATUS_READY = "ready"          # 索引可用
STATUS_STALE = "stale"          # 索引可用但已落后（需增量/重建）
STATUS_DENIED = "denied"        # 卷打不开（权限/争用），索引保留
STATUS_ERROR = "error"          # 其它错误，索引保留
STATUS_UNSUPPORTED = "unsupported"  # 非 NTFS / 无 journal 且无索引


def default_index_dir() -> str:
    """默认索引目录：``FOOLPROOF_INDEX_DIR`` → ``%LOCALAPPDATA%`` → 用户主目录。

    刻意**不放仓库内**：索引是运行期缓存（单卷可达数百 MB），不入版本控制。
    """
    override = os.environ.get("FOOLPROOF_INDEX_DIR")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "foolproof-operation", "index")


class IndexService(Service):
    """USN 索引服务（插件行名 ``fileindex``）。

    配置项（``config``）：``volumes``（卷列表，缺省=全部 NTFS 盘）、``index_dir``、
    ``batch_size``、``max_records``（建库记录上限，None=不限）、``auto_build``（启动即建库）。
    """

    name = "fileindex"
    inject: Sequence[str] = ()

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        cfg: Dict[str, Any] = self.config
        self.index_dir = cfg.get("index_dir") or default_index_dir()
        self.batch_size = int(cfg.get("batch_size") or DEFAULT_BATCH_SIZE)
        # 0 / None 都表示「不限」（0 曾被误当成「上限 0」而立即停止枚举）
        self.max_records = cfg.get("max_records") or None
        self.deadline_seconds = float(cfg.get("deadline_seconds") or 300.0)
        volumes = cfg.get("volumes")
        self.volumes: List[str] = [normalize_volume(v) for v in volumes] if volumes else []
        self._stores: Dict[str, IndexStore] = {}
        self._locks: Dict[str, threading.RLock] = {}
        self._status: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._tasks: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._started = False

    # ── 生命周期（内核钩子）───────────────────────────

    def start(self) -> None:
        """启动后台工作者；``auto_build`` 为真时排队建库。"""
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run_worker, name="foolproof-fileindex", daemon=True
        )
        self._worker.start()
        if self.config.get("auto_build"):
            for volume in self.detected_volumes():
                self.ensure_index(volume, background=True)

    def stop(self) -> None:
        """停止工作者并关闭全部索引库（幂等）。"""
        self._started = False
        self._stop.set()
        try:
            self._tasks.put_nowait(("__stop__", ""))
        except Exception:
            pass
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        for store in self._stores.values():
            try:
                store.close()
            except Exception:
                pass
        self._stores.clear()

    # ── 卷与库 ───────────────────────────────────────

    def detected_volumes(self) -> List[str]:
        """本机 NTFS 盘符（未显式配置 volumes 时使用）。"""
        if self.volumes:
            return list(self.volumes)
        try:
            return list_ntfs_volumes()
        except UsnError:
            return []

    def db_path(self, volume: str) -> str:
        return os.path.join(self.index_dir, "{0}.db".format(normalize_volume(volume).replace(":", "")))

    def store(self, volume: str, *, create: bool = True) -> IndexStore:
        """取（或打开）某卷的索引库。"""
        volume = normalize_volume(volume)
        with self._lock(volume):
            store = self._stores.get(volume)
            if store is not None:
                return store
            path = self.db_path(volume)
            exists = os.path.isfile(path)
            if not exists and not create:
                raise StoreError("卷 {0} 尚无索引库: {1}".format(volume, path))
            try:
                store = IndexStore.open(volume, path, batch_size=self.batch_size)
            except StoreError as exc:
                raise StoreError("索引库打开失败（{0}）: {1}".format(path, exc)) from exc
            self._stores[volume] = store
            if volume not in self._status:
                self._status[volume] = {
                    "volume": volume,
                    "status": STATUS_READY if exists else STATUS_MISSING,
                    "records": store.count() if exists else 0,
                    "path": path,
                    "detail": "",
                    "updated": time.strftime("%H:%M:%S"),
                }
            elif exists:
                self._status[volume]["status"] = STATUS_READY
            return store

    def _lock(self, volume: str) -> threading.RLock:
        lock = self._locks.get(volume)
        if lock is None:
            lock = self._locks.setdefault(volume, threading.RLock())
        return lock

    def _set_status(self, volume: str, status: str, **extra: Any) -> None:
        entry = self._status.setdefault(volume, {"volume": volume})
        entry.update(status=status, updated=time.strftime("%H:%M:%S"))
        if extra:
            entry.update(extra)

    def status(self, *, probe: bool = True) -> Dict[str, Dict[str, Any]]:
        """各卷状态快照（含未建索引的卷）。

        ``probe=True`` 时对**尚未登记过**的卷查一次磁盘上是否已有索引库（``create=False``，
        **不会建库、不会产生空库文件**）—— 否则新进程里所有卷都会显示 ``missing``，
        导致 ``index`` 后端被误判为不可用而整条链静默回退。
        """
        for volume in self.detected_volumes():
            if volume in self._status:
                continue
            if not probe:
                self._set_status(
                    volume, STATUS_MISSING, records=0, path=self.db_path(volume), detail="未探测（probe=False）"
                )
                continue
            try:
                self.store(volume, create=False)  # 已存在则打开并登记 ready + 行数
            except StoreError:
                self._set_status(
                    volume, STATUS_MISSING, records=0, path=self.db_path(volume), detail="尚无索引"
                )
        return {volume: dict(entry) for volume, entry in self._status.items()}

    # ── 建库 ─────────────────────────────────────────

    def ensure_index(self, volume: str, *, background: bool = True, force: bool = False) -> Dict[str, Any]:
        """确保某卷有可用索引；返回该卷状态。``background=True`` 时排队异步执行。"""
        volume = normalize_volume(volume)
        if background:
            self._tasks.put((volume, "ensure"))
            self._set_status(volume, STATUS_BUILDING, detail="已排队")
            return dict(self._status[volume])
        return self.build(volume, force=force)

    def build(self, volume: str, *, force: bool = False, seconds: Optional[float] = None) -> Dict[str, Any]:
        """全量建库（同步）。失败降级为状态报告，不抛栈到调用方。"""
        volume = normalize_volume(volume)
        path = self.db_path(volume)
        started = time.monotonic()
        try:
            with UsnVolume(volume) as vol:
                journal = vol.query_journal()
                store = self.store(volume)
                if not force:
                    rebuild, reason = store.needs_rebuild(journal)
                    if not rebuild and store.count() > 0:
                        self._set_status(
                            volume, STATUS_READY, records=store.count(), detail="索引已最新", path=path
                        )
                        return dict(self._status[volume])
                else:
                    reason = "强制重建"
                self._set_status(volume, STATUS_BUILDING, records=0, detail=reason, path=path)
                with self._lock(volume):
                    store.begin_build()
                    limit = self.max_records
                    deadline = time.monotonic() + (seconds or self.deadline_seconds)
                    rows = store.add_records(
                        vol.enumerate_mft(deadline=deadline, limit=limit, should_stop=self._stop.is_set)
                    )
                    store.finish_build(
                        journal_id=journal.journal_id if journal else None,
                        next_usn=journal.next_usn if journal else None,
                    )
                elapsed = time.monotonic() - started
                self._set_status(
                    volume,
                    STATUS_READY,
                    records=rows,
                    detail="重建：{0}".format(reason) if reason else "建库完成",
                    path=path,
                    seconds=round(elapsed, 2),
                    rows_per_second=round(rows / elapsed, 0) if elapsed else 0,
                )
        except VolumeAccessError as exc:
            self._set_status(volume, STATUS_DENIED, detail=str(exc), path=path)
        except JournalNotActiveError as exc:
            self._set_status(volume, STATUS_UNSUPPORTED, detail=str(exc), path=path)
        except (UsnError, StoreError) as exc:
            self._set_status(volume, STATUS_ERROR, detail=str(exc), path=path)
        return dict(self._status.get(volume, {"volume": volume, "status": STATUS_ERROR}))

    # ── 增量 ─────────────────────────────────────────

    def sync(self, volume: str, *, limit: Optional[int] = None) -> Dict[str, Any]:
        """增量同步某卷（同步执行）。无 journal 时返回 ``unsupported``（不重建）。"""
        volume = normalize_volume(volume)
        try:
            with UsnVolume(volume) as vol:
                journal = vol.query_journal()
                if journal is None:
                    self._set_status(volume, STATUS_UNSUPPORTED, detail="该卷无 USN journal")
                    return dict(self._status[volume])
                store = self.store(volume)
                rebuild, reason = store.needs_rebuild(journal)
                if rebuild:
                    self._set_status(volume, STATUS_STALE, detail="需重建：" + reason)
                    return dict(self._status[volume])
                stored_next = store.get_meta("next_usn")
                start_usn = int(stored_next) if stored_next else journal.first_usn
                with self._lock(volume):
                    counts = store.apply_changes(
                        vol.read_journal(start_usn, journal_id=journal.journal_id, limit=limit)
                    )
                    store.set_journal_state(
                        journal_id=journal.journal_id, next_usn=journal.next_usn
                    )
                self._set_status(
                    volume,
                    STATUS_READY,
                    records=store.count(),
                    detail="增量 upsert={upsert} remove={remove} ignore={ignore}".format(**counts),
                    counts=counts,
                )
        except VolumeAccessError as exc:
            self._set_status(volume, STATUS_DENIED, detail=str(exc))
        except JournalNotActiveError as exc:
            self._set_status(volume, STATUS_UNSUPPORTED, detail=str(exc))
        except (UsnError, StoreError) as exc:
            self._set_status(volume, STATUS_ERROR, detail=str(exc))
        return dict(self._status.get(volume, {"volume": volume, "status": STATUS_ERROR}))

    # ── 查询 ─────────────────────────────────────────

    def search(
        self,
        names: Iterable[str],
        *,
        volumes: Optional[Iterable[str]] = None,
        limit_per_name: Optional[int] = None,
        resolve: bool = True,
    ) -> Dict[str, Any]:
        """按文件名批量查询，返回 ``{"hits": {name: [{path, index, volume, is_dir}]}, "missing": [...]}``。

        缺索引的卷进 ``missing``（**显式**，不是静默空结果）。
        """
        wanted = [name for name in names]
        result: Dict[str, Any] = {"hits": {name: [] for name in wanted}, "missing": [], "volumes": []}
        for volume in [normalize_volume(v) for v in (volumes or self.detected_volumes())]:
            try:
                store = self.store(volume, create=False)
            except StoreError:
                result["missing"].append(volume)
                continue
            if store.interrupted:
                result["missing"].append(volume)
                continue
            result["volumes"].append(volume)
            with self._lock(volume):
                found = store.find_by_names(wanted, limit_per_name=limit_per_name)
                for name, nodes in found.items():
                    for node in nodes:
                        result["hits"][name].append(
                            {
                                "volume": volume,
                                "index": node.index,
                                "is_dir": node.is_directory,
                                "path": store.resolve_path(node.index) if resolve else node.name,
                            }
                        )
        return result

    def find(self, name: str, **kwargs: Any) -> List[str]:
        """便捷方法：返回该文件名的全部路径（去 ``None``）。"""
        hits = self.search([name], **kwargs)["hits"].get(name, [])
        return [hit["path"] for hit in hits if hit.get("path")]

    # ── 后台工作者 ───────────────────────────────────

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                volume, mode = self._tasks.get(timeout=0.2)
            except queue.Empty:
                continue
            if volume == "__stop__":
                return
            try:
                if mode == "ensure":
                    self._ensure_and_sync(volume)
                elif mode == "sync":
                    self.sync(volume)
            except Exception as exc:  # 工作者绝不因单卷失败退出
                self._set_status(volume, STATUS_ERROR, detail=str(exc))

    def _ensure_and_sync(self, volume: str) -> None:
        """先建库（若需要），再增量补齐到最新。"""
        state = self.build(volume)
        if state.get("status") == STATUS_READY:
            self.sync(volume)

    def __repr__(self) -> str:
        return "<IndexService dir={0} volumes={1} status={2}>".format(
            self.index_dir, self.detected_volumes(), len(self._status)
        )
