"""FRN → 路径还原：父链回溯 + rename/delete 增量语义。

USN/MFT 记录只给出「自己的名字 + 父目录的 FRN」，路径必须沿父链回溯拼出。
两类输入共用同一张表：

- **全量**：``add(record)``（``FSCTL_ENUM_USN_DATA`` 的每条记录）；
- **增量**：``apply_change(record)``（``FSCTL_READ_USN_JOURNAL`` 的变更记录），
  按 reason 位区分新增 / 删除 / 改名（``FILE_RENAME_OLD_NAME`` 忽略，紧随其后的
  ``FILE_RENAME_NEW_NAME`` 才是新名）。

主键取 FRN 的**低 48 位（MFT 记录号）**：高 16 位是序列号，记录被复用时序列号会变，
以记录号为键可让「删除后复用」自然表现为覆盖更新（详情见 ``docs/usn-indexer.md`` §4.1）。

**不做缓存、只做回溯**：目录改名后，其子树路径会因父链指向该目录而自动反映新名，
不存在陈旧缓存问题；深度通常 < 20，回溯成本可忽略。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Set, Tuple

from .usn import (
    MFT_ROOT_INDEX,
    REASON_FILE_CREATE,
    REASON_FILE_DELETE,
    REASON_RENAME_NEW_NAME,
    REASON_RENAME_OLD_NAME,
    UsnRecord,
)

__all__ = ["PathNode", "PathResolver", "ResolveStats"]


class PathNode:
    """索引表中的一个条目（文件或目录）。

    刻意用 ``__slots__`` 而非 dataclass：单卷可达数百万条，节点内存是硬约束
    （无 slot 的 dataclass 每条约 200+ 字节，且多一层 ``__dict__``）。
    """

    __slots__ = ("index", "name", "parent_index", "is_directory", "usn")

    def __init__(
        self,
        index: int,
        name: str,
        parent_index: int,
        is_directory: bool = False,
        usn: int = 0,
    ) -> None:
        self.index = index
        self.name = name
        self.parent_index = parent_index
        self.is_directory = is_directory
        self.usn = usn

    def key(self) -> Tuple[str, int]:
        return (self.name, self.parent_index)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<PathNode {0} idx={1} parent={2} dir={3}>".format(
            self.name, self.index, self.parent_index, self.is_directory
        )


@dataclass
class ResolveStats:
    """索引表规模与还原质量统计。"""

    nodes: int = 0
    directories: int = 0
    unresolved: int = 0
    cycles: int = 0
    max_depth: int = 0


class PathResolver:
    """FRN 索引表 + 路径还原。

    用法::

        resolver = PathResolver("G:")
        for record in vol.enumerate_mft():
            resolver.add(record)
        resolver.resolve(12345)          # -> 'G:\\Python314\\python.exe'
        resolver.apply_change(delta)     # 增量：新增/改名/删除
    """

    def __init__(self, volume_label: str, *, track_max_depth: bool = True) -> None:
        if not volume_label:
            raise ValueError("volume_label 不能为空（例如 'G:'）")
        self.volume_label = volume_label.rstrip("\\/")
        self.track_max_depth = track_max_depth
        self._nodes: Dict[int, PathNode] = {}
        self._cycles: Set[int] = set()
        self._max_depth = 0
        self._root_seen = False
        self.added = 0
        self.removed = 0
        self.renamed = 0
        self.ignored = 0

    # ── 表规模 ───────────────────────────────────────

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, index: object) -> bool:
        return index in self._nodes

    def node(self, index: int) -> Optional[PathNode]:
        return self._nodes.get(index)

    def nodes(self) -> Iterator[PathNode]:
        return iter(self._nodes.values())

    # ── 全量录入 ─────────────────────────────────────

    def add(self, record: UsnRecord) -> str:
        """录入一条全量记录。返回 ``'add'`` / ``'update'`` / ``'ignore'``。"""
        index = record.index
        if index == MFT_ROOT_INDEX:
            self._root_seen = True
            return "ignore"
        if not record.name:
            self.ignored += 1
            return "ignore"
        node = PathNode(
            index=index,
            name=record.name,
            parent_index=record.parent_index,
            is_directory=record.is_directory,
            usn=record.usn,
        )
        existing = self._nodes.get(index)
        if existing is None:
            self._nodes[index] = node
            self.added += 1
            return "add"
        if existing.name == node.name and existing.parent_index == node.parent_index:
            existing.usn = node.usn
            existing.is_directory = node.is_directory
            self.ignored += 1
            return "ignore"
        self._nodes[index] = node
        self.renamed += 1
        return "update"

    # ── 增量变更 ─────────────────────────────────────

    def apply_change(self, record: UsnRecord) -> str:
        """应用一条 journal 变更。返回 ``'add'`` / ``'update'`` / ``'remove'`` / ``'ignore'``。

        语义（按 reason 位）：

        - ``FILE_DELETE`` → 摘除该 FRN（``'remove'``）；
        - ``FILE_RENAME_OLD_NAME`` → 忽略（旧名记录，随后必有 NEW_NAME）；
        - ``FILE_RENAME_NEW_NAME`` → 覆盖为新名/新父（``'update'``，或此前未见过的 ``'add'``）；
        - ``FILE_CREATE`` → 新增（``'add'``）；
        - 其余（数据/属性/安全变更）→ 仅在名称或父发生变化时更新，否则 ``'ignore'``。
        """
        index = record.index
        if index == MFT_ROOT_INDEX:
            self._root_seen = True
            return "ignore"
        reason = record.reason
        if reason & REASON_FILE_DELETE:
            return "remove" if self.remove(index) else "ignore"
        if reason & REASON_RENAME_OLD_NAME and not (reason & REASON_RENAME_NEW_NAME):
            self.ignored += 1
            return "ignore"
        if not record.name:
            self.ignored += 1
            return "ignore"
        return self.add(record)

    def apply_changes(self, records: Iterator[UsnRecord]) -> Dict[str, int]:
        """批量应用变更，返回各动作计数（增量同步的落库依据）。"""
        counts = {"add": 0, "update": 0, "remove": 0, "ignore": 0}
        for record in records:
            action = self.apply_change(record)
            counts[action] = counts.get(action, 0) + 1
        return counts

    def remove(self, index: int) -> bool:
        """摘除某条目（删除事件）。目录被删时其子项会因父缺失而变为不可还原。"""
        if index not in self._nodes:
            return False
        del self._nodes[index]
        self.removed += 1
        return True

    # ── 路径还原 ─────────────────────────────────────

    def resolve(self, index: int) -> Optional[str]:
        """FRN 记录号 → 绝对路径；父链缺失或成环时返回 ``None``（不抛错）。"""
        if index == MFT_ROOT_INDEX:
            return self.volume_label + "\\"
        parts: List[str] = []
        seen: Set[int] = set()
        cursor = index
        depth = 0
        while cursor != MFT_ROOT_INDEX:
            if cursor in seen:
                self._cycles.add(cursor)
                return None
            seen.add(cursor)
            node = self._nodes.get(cursor)
            if node is None:
                return None  # 父链断裂（父目录未录入 / 已被删除）
            parts.append(node.name)
            cursor = node.parent_index
            depth += 1
            if depth > 512:  # 防御：异常深链
                return None
        if self.track_max_depth and depth > self._max_depth:
            self._max_depth = depth
        parts.reverse()
        return self.volume_label + "\\" + "\\".join(parts)

    def resolve_record(self, record: UsnRecord) -> Optional[str]:
        """便捷方法：先录入再还原（不改变表，只补本条）。"""
        node = self._nodes.get(record.index)
        if node is None:
            return None
        return self.resolve(record.index)

    def relative_parts(self, index: int) -> Optional[List[str]]:
        """FRN → 相对路径片段（不含盘符），便于按层判据匹配。"""
        path = self.resolve(index)
        if path is None:
            return None
        head = self.volume_label + "\\"
        rest = path[len(head) :] if path.startswith(head) else path
        return [p for p in rest.split("\\") if p]

    # ── 统计 ─────────────────────────────────────────

    def unresolved_count(self, sample: int = 0) -> int:
        """统计无法还原路径的条目数（父链断裂）。``sample > 0`` 时只抽查前 N 条。"""
        count = 0
        for i, index in enumerate(self._nodes):
            if sample and i >= sample:
                break
            if self.resolve(index) is None:
                count += 1
        return count

    def stats(self, *, sample: int = 0) -> ResolveStats:
        """统计规模与还原质量；``sample > 0`` 时未还原数只抽查前 N 条（大表提速）。"""
        directories = sum(1 for node in self._nodes.values() if node.is_directory)
        return ResolveStats(
            nodes=len(self._nodes),
            directories=directories,
            unresolved=self.unresolved_count(sample=sample),
            cycles=len(self._cycles),
            max_depth=self._max_depth,
        )

    def __repr__(self) -> str:
        return "<PathResolver {0} nodes={1} dirs-tracked={2}>".format(
            self.volume_label, len(self._nodes), self.track_max_depth
        )
