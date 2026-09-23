"""NTFS USN/MFT 索引层（**只读**枚举源 + 本地索引库，零第三方依赖）。

- ``usn``：USN/MFT 只读客户端（ctypes 直调 Win32）+ 纯解析逻辑；
- ``paths``：FRN → 路径还原（父链回溯 + rename/delete 增量语义）；
- ``store``：SQLite 按卷分库（批量事务落库 + 名字/父链查询 + 重建判定）；
- ``probe``：实盘基准探针（卷 × 记录数 × 命中 × 耗时；``--store`` 模式产出落库/查询基准）；
- ``service``：索引服务（后台建库 + 增量 + 查询），**继承内核 ``core.context.Service``**，
  故刻意不在本 ``__init__`` 中导入，以免 ``import index`` 连带引入内核层；
  需要时显式 ``from index.service import IndexService``。

原理、实测与许可记账见 ``docs/usn-indexer.md``。
"""

from __future__ import annotations

from .paths import PathNode, PathResolver, ResolveStats
from .store import SCHEMA_VERSION, IndexStore, NodeRow, StoreError, StoreStats
from .usn import (
    MAX_USN,
    MFT_ROOT_INDEX,
    JournalInfo,
    JournalNotActiveError,
    RecordBatch,
    UsnError,
    UsnRecord,
    UsnVolume,
    VolumeAccessError,
    describe_reason,
    device_path,
    is_ntfs_volume,
    list_ntfs_volumes,
    normalize_volume,
    parse_batch,
    parse_usn_record,
)

__all__ = [
    "UsnVolume",
    "UsnRecord",
    "JournalInfo",
    "RecordBatch",
    "UsnError",
    "VolumeAccessError",
    "JournalNotActiveError",
    "PathResolver",
    "PathNode",
    "ResolveStats",
    "IndexStore",
    "NodeRow",
    "StoreStats",
    "StoreError",
    "SCHEMA_VERSION",
    "normalize_volume",
    "device_path",
    "is_ntfs_volume",
    "list_ntfs_volumes",
    "parse_usn_record",
    "parse_batch",
    "describe_reason",
    "MAX_USN",
    "MFT_ROOT_INDEX",
]
