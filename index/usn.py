r"""NTFS USN/MFT **只读**客户端（ctypes 直调 Win32，零第三方依赖）。

实现的是「Everything 原理」而非其源码：``FSCTL_ENUM_USN_DATA`` 全量枚举 MFT 记录建索引，
``FSCTL_READ_USN_JOURNAL`` 按 USN 增量同步。原理、实测与许可记账见 ``docs/usn-indexer.md``。

**安全边界（本仓红线）**：

- 只打开卷句柄读取，**绝不调用** ``FSCTL_CREATE_USN_JOURNAL``（改卷状态的写操作）；
- 无 journal 的卷 → ``query_journal()`` 返回 ``None``，增量降级为「重扫该卷」；
- 权限不足（如非提权的系统盘 ``\\.\C:``）→ 抛 ``VolumeAccessError``，由上层显式降级，
  **绝不静默漏数据**。

纯解析逻辑（``parse_batch`` / ``parse_usn_record`` / ``normalize_volume``）与 Win32 调用分离，
因此可在任意平台单测；只有真正打开卷时才需要 Windows。
"""

from __future__ import annotations

import ctypes
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "UsnError",
    "VolumeAccessError",
    "JournalNotActiveError",
    "JournalInfo",
    "UsnRecord",
    "RecordBatch",
    "UsnVolume",
    "normalize_volume",
    "device_path",
    "is_ntfs_volume",
    "list_ntfs_volumes",
    "parse_usn_record",
    "parse_batch",
    "describe_reason",
    "MAX_USN",
    "MFT_ROOT_INDEX",
    "FRN_INDEX_MASK",
    "REASON_NAMES",
]

# ── Win32 常量 ──────────────────────────────────────────────────────────

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_ENUM_USN_DATA = 0x000900B3
FSCTL_READ_USN_JOURNAL = 0x000900BB
#: 本仓**不使用**（写操作，会改变卷状态）——仅在注释中留档以便审计。
FSCTL_CREATE_USN_JOURNAL = 0x000900E7

ERROR_SUCCESS = 0
ERROR_INVALID_FUNCTION = 1
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_HANDLE = 6
ERROR_HANDLE_EOF = 38
ERROR_JOURNAL_DELETE_IN_PROGRESS = 1178
ERROR_JOURNAL_NOT_ACTIVE = 1179

FILE_ATTRIBUTE_DIRECTORY = 0x00000010

#: USN 的合法上界（Enum 全量枚举须用它，而不是 journal 的 FirstUsn）。
MAX_USN = 0x7FFFFFFFFFFFFFFF
#: NTFS 根目录的 MFT 记录号。
MFT_ROOT_INDEX = 5
#: FRN 低 48 位 = MFT 记录号，高 16 位 = 序列号。
FRN_INDEX_MASK = 0xFFFFFFFFFFFF

#: USN reason 位（增量同步判定增/删/改名用）。
REASON_NAMES: Dict[int, str] = {
    0x00000001: "DATA_OVERWRITE",
    0x00000002: "DATA_EXTEND",
    0x00000004: "DATA_TRUNCATION",
    0x00000100: "FILE_CREATE",
    0x00000200: "FILE_DELETE",
    0x00000400: "EA_CHANGE",
    0x00000800: "SECURITY_CHANGE",
    0x00001000: "FILE_RENAME_OLD_NAME",
    0x00002000: "FILE_RENAME_NEW_NAME",
    0x00004000: "INDEXABLE_CHANGE",
    0x00008000: "BASIC_INFO_CHANGE",
    0x00010000: "HARD_LINK_CHANGE",
    0x00020000: "COMPRESSION_CHANGE",
    0x00040000: "ENCRYPTION_CHANGE",
    0x00080000: "OBJECT_ID_CHANGE",
    0x00100000: "REPARSE_POINT_CHANGE",
    0x00200000: "STREAM_CHANGE",
    0x00400000: "TRANSACTED_CHANGE",
    0x80000000: "CLOSE",
}
REASON_FILE_CREATE = 0x00000100
REASON_FILE_DELETE = 0x00000200
REASON_RENAME_OLD_NAME = 0x00001000
REASON_RENAME_NEW_NAME = 0x00002000


# ── 错误类型 ────────────────────────────────────────────────────────────


class UsnError(Exception):
    """USN 客户端错误基类。"""


class VolumeAccessError(UsnError):
    """卷句柄无法打开（权限不足 / 卷不存在 / 非 NTFS）。

    非提权的系统盘会走到这里 —— 上层应据此**显式降级**，不得静默跳过该卷。
    """

    def __init__(self, volume: str, code: int, detail: str = "") -> None:
        self.volume = volume
        self.code = code
        self.detail = detail or _winerror_text(code)
        super().__init__(
            "无法打开卷 {0}（Win32 错误 {1}: {2}）；"
            "若为系统盘，需以管理员身份运行才能建立索引".format(volume, code, self.detail)
        )


class JournalNotActiveError(UsnError):
    """卷存在但未启用 USN journal（增量不可用；本仓不主动创建）。"""


def _winerror_text(code: int) -> str:
    """Win32 错误码 → 可读文本（失败时退回数字描述）。"""
    mapping = {
        ERROR_ACCESS_DENIED: "拒绝访问",
        ERROR_INVALID_FUNCTION: "功能不受支持",
        ERROR_INVALID_HANDLE: "句柄无效",
        ERROR_HANDLE_EOF: "已到枚举末尾",
        ERROR_JOURNAL_NOT_ACTIVE: "该卷未启用 USN journal",
        ERROR_JOURNAL_DELETE_IN_PROGRESS: "USN journal 正在删除",
    }
    if code in mapping:
        return mapping[code]
    try:  # pragma: no cover - 依赖系统消息表
        return ctypes.FormatError(code).strip() or "未知错误"
    except Exception:  # pragma: no cover
        return "未知错误"


# ── 卷路径归一 ──────────────────────────────────────────────────────────


def normalize_volume(volume: str) -> str:
    """``'G:'`` / ``'G:\\'`` / ``'\\\\.\\G:'`` → ``'G:'``（盘符统一为大写）。"""
    if not isinstance(volume, str) or not volume.strip():
        raise UsnError("卷名不能为空")
    text = volume.strip()
    if text.startswith("\\\\"):
        text = text[4:]
    text = text.rstrip("\\/")
    if len(text) == 2 and text[1] == ":":
        return text[0].upper() + ":"
    if len(text) == 1:
        return text.upper() + ":"
    return text


def device_path(volume: str) -> str:
    """卷名 → 设备路径 ``\\\\.\\G:``（``CreateFileW`` 所需）。"""
    return "\\\\.\\" + normalize_volume(volume)


# ── Win32 结构 ──────────────────────────────────────────────────────────


class _USN_JOURNAL_DATA_V0(ctypes.Structure):
    _fields_ = [
        ("UsnJournalID", ctypes.c_ulonglong),
        ("FirstUsn", ctypes.c_longlong),
        ("NextUsn", ctypes.c_longlong),
        ("LowestValidUsn", ctypes.c_longlong),
        ("MaxUsn", ctypes.c_longlong),
        ("MaximumSize", ctypes.c_ulonglong),
        ("AllocationDelta", ctypes.c_ulonglong),
    ]


class _MFT_ENUM_DATA_V0(ctypes.Structure):
    _fields_ = [
        ("StartFileReferenceNumber", ctypes.c_ulonglong),
        ("LowUsn", ctypes.c_longlong),
        ("HighUsn", ctypes.c_longlong),
    ]


class _READ_USN_JOURNAL_DATA_V0(ctypes.Structure):
    _fields_ = [
        ("StartUsn", ctypes.c_longlong),
        ("ReasonMask", ctypes.c_uint32),
        ("ReturnOnlyOnClose", ctypes.c_uint32),
        ("Timeout", ctypes.c_ulonglong),
        ("BytesToWaitFor", ctypes.c_ulonglong),
        ("UsnJournalID", ctypes.c_ulonglong),
    ]


def _kernel32() -> Any:
    """惰性加载 kernel32（非 Windows 平台给出明确错误，便于跨平台单测）。"""
    if sys.platform != "win32":
        raise UsnError("USN 索引仅支持 Windows/NTFS（当前平台: {0}）".format(sys.platform))
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    k32.DeviceIoControl.restype = ctypes.c_int
    k32.DeviceIoControl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


# ── 记录解析（纯函数，可跨平台单测）────────────────────────────────────


@dataclass(frozen=True)
class UsnRecord:
    """一条 MFT/USN 记录（当前状态的名称与位置）。"""

    frn: int
    parent_frn: int
    usn: int
    timestamp: int
    reason: int
    attributes: int
    name: str
    major: int = 2
    minor: int = 0

    @property
    def index(self) -> int:
        """MFT 记录号（FRN 低 48 位）—— 本仓用它作索引主键。"""
        return self.frn & FRN_INDEX_MASK

    @property
    def parent_index(self) -> int:
        return self.parent_frn & FRN_INDEX_MASK

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & FILE_ATTRIBUTE_DIRECTORY)

    @property
    def deleted(self) -> bool:
        return bool(self.reason & REASON_FILE_DELETE)

    @property
    def renamed(self) -> bool:
        return bool(self.reason & REASON_RENAME_NEW_NAME)

    def __str__(self) -> str:  # pragma: no cover - 仅调试用
        return "<UsnRecord {0}{1} idx={2} parent={3} reason={4}>".format(
            self.name, "/" if self.is_directory else "", self.index, self.parent_index,
            describe_reason(self.reason),
        )


def describe_reason(reason: int) -> List[str]:
    """reason 位 → 名称列表（未知位以 ``0x...`` 呈现，便于排查）。"""
    names = [name for bit, name in sorted(REASON_NAMES.items()) if reason & bit]
    known = 0
    for bit in REASON_NAMES:
        known |= bit
    if reason & ~known:
        names.append(hex(reason & ~known))
    return names


def parse_usn_record(data: bytes, offset: int = 0) -> Tuple[Optional[UsnRecord], int]:
    """解析单条 ``USN_RECORD_V2/V3``。

    返回 ``(记录或 None, 下一条的偏移)``。``None`` 表示该位置无法解析
    （截断缓冲 / 不支持的 V4 Extent 记录），调用方计入 ``skipped``。

    V4（范围跟踪 Extent）本仓不解析：它只在显式请求 range tracking 时出现，
    本仓的 ENUM/READ 调用不带该标志。
    """
    if offset + 8 > len(data):
        return None, len(data)
    record_length, major, minor = struct.unpack_from("<IHH", data, offset)
    if record_length <= 0 or offset + record_length > len(data):
        return None, len(data)
    end = offset + record_length
    if major == 2:
        (frn, parent) = struct.unpack_from("<QQ", data, offset + 8)
        (usn, timestamp) = struct.unpack_from("<qq", data, offset + 24)
        reason, _source, _security, attributes = struct.unpack_from("<IIII", data, offset + 40)
        name_len, name_off = struct.unpack_from("<HH", data, offset + 56)
    elif major == 3:
        frn = int.from_bytes(data[offset + 8 : offset + 24], "little")
        parent = int.from_bytes(data[offset + 24 : offset + 40], "little")
        (usn, timestamp) = struct.unpack_from("<qq", data, offset + 40)
        reason, _source, _security, attributes = struct.unpack_from("<IIII", data, offset + 56)
        name_len, name_off = struct.unpack_from("<HH", data, offset + 72)
    else:
        return None, end
    raw_name = data[offset + name_off : offset + name_off + name_len]
    name = raw_name.decode("utf-16-le", "replace")
    return (
        UsnRecord(
            frn=frn,
            parent_frn=parent,
            usn=usn,
            timestamp=timestamp,
            reason=reason,
            attributes=attributes,
            name=name,
            major=major,
            minor=minor,
        ),
        end,
    )


@dataclass
class RecordBatch:
    """一个 ENUM/READ 输出缓冲区的解析结果。

    ``cursor`` 是缓冲区前 8 字节的续游标（ENUM 里是下一个 FRN，READ 里是下一个 USN）。
    """

    cursor: int
    records: List[UsnRecord]
    skipped: int = 0
    complete: bool = True

    def __len__(self) -> int:
        return len(self.records)


def parse_batch(data: bytes) -> RecordBatch:
    """解析 ``DeviceIoControl`` 返回的整个缓冲区（8 字节游标 + 若干记录）。"""
    if len(data) < 8:
        return RecordBatch(cursor=0, records=[], skipped=0, complete=False)
    cursor = struct.unpack_from("<Q", data, 0)[0]
    records: List[UsnRecord] = []
    skipped = 0
    offset = 8
    complete = True
    while offset + 8 <= len(data):
        record, nxt = parse_usn_record(data, offset)
        if record is None:
            complete = False
            skipped += 1
            break
        records.append(record)
        offset = nxt
    return RecordBatch(cursor=cursor, records=records, skipped=skipped, complete=complete)


# ── 卷信息 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JournalInfo:
    """``FSCTL_QUERY_USN_JOURNAL`` 的结果（用于判定索引是否失效）。"""

    journal_id: int
    first_usn: int
    next_usn: int
    lowest_valid_usn: int
    max_usn: int
    maximum_size: int
    allocation_delta: int

    @property
    def journal_span(self) -> int:
        """journal 窗口宽度 —— 只用 ``first_usn`` 当 LowUsn 会漏掉窗口外的既有文件。"""
        return max(0, self.next_usn - self.first_usn)


# ── 卷句柄 ──────────────────────────────────────────────────────────────


class UsnVolume:
    """一个卷的只读 USN/MFT 句柄（上下文管理器）。

    用法::

        with UsnVolume("G:") as vol:
            info = vol.query_journal()
            for record in vol.enumerate_mft(limit=10000):
                print(record.index, record.name)
    """

    def __init__(self, volume: str, *, buffer_size: int = 0x40000) -> None:
        self.volume = normalize_volume(volume)
        self.device = device_path(self.volume)
        self.buffer_size = int(buffer_size)
        self._handle: Optional[int] = None
        self._k32: Any = None
        self.last_error: int = 0
        self.calls = 0
        self.records_seen = 0
        self.records_skipped = 0

    # ── 生命周期 ─────────────────────────────────────

    def open(self) -> "UsnVolume":
        """打开卷句柄；失败抛 ``VolumeAccessError``（含错误码与降级提示）。"""
        if self._handle is not None:
            return self
        k32 = _kernel32()
        handle = k32.CreateFileW(
            self.device,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise VolumeAccessError(self.volume, ctypes.get_last_error())
        self._k32 = k32
        self._handle = handle
        return self

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._k32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    @property
    def opened(self) -> bool:
        return self._handle is not None

    def __enter__(self) -> "UsnVolume":
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── 日志元数据 ───────────────────────────────────

    def query_journal(self) -> Optional[JournalInfo]:
        """读取 journal 元数据；该卷无 journal 时返回 ``None``（**不创建**）。"""
        self.open()
        data = _USN_JOURNAL_DATA_V0()
        returned = ctypes.c_uint32(0)
        ok = self._k32.DeviceIoControl(
            self._handle,
            FSCTL_QUERY_USN_JOURNAL,
            None,
            0,
            ctypes.byref(data),
            ctypes.sizeof(data),
            ctypes.byref(returned),
            None,
        )
        if not ok:
            code = ctypes.get_last_error()
            self.last_error = code
            if code in (ERROR_JOURNAL_NOT_ACTIVE, ERROR_INVALID_FUNCTION, ERROR_JOURNAL_DELETE_IN_PROGRESS):
                return None
            raise UsnError(
                "FSCTL_QUERY_USN_JOURNAL 失败（{0}: {1}）".format(code, _winerror_text(code))
            )
        return JournalInfo(
            journal_id=data.UsnJournalID,
            first_usn=data.FirstUsn,
            next_usn=data.NextUsn,
            lowest_valid_usn=data.LowestValidUsn,
            max_usn=data.MaxUsn,
            maximum_size=data.MaximumSize,
            allocation_delta=data.AllocationDelta,
        )

    # ── 全量枚举 ─────────────────────────────────────

    def enumerate_mft(
        self,
        *,
        low_usn: int = 0,
        high_usn: int = MAX_USN,
        limit: Optional[int] = None,
        deadline: Optional[float] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        start_frn: int = 0,
    ) -> Iterator[UsnRecord]:
        """枚举 MFT 记录（``FSCTL_ENUM_USN_DATA``），逐条 yield。

        ``low_usn`` 默认 **0**：用 journal 的 ``FirstUsn`` 只能拿到窗口内记录
        （实测 G: 仅 12,071 条即 EOF），见 ``docs/usn-indexer.md`` §3 结论。

        ``deadline`` 为 ``time.monotonic()`` 时间戳；``should_stop`` 供外部中断
        （GUI 取消 / 后台线程停止）。
        """
        self.open()
        med = _MFT_ENUM_DATA_V0(start_frn, low_usn, high_usn)
        buf = ctypes.create_string_buffer(self.buffer_size)
        returned = ctypes.c_uint32(0)
        yielded = 0
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return
            if should_stop is not None and should_stop():
                return
            ok = self._k32.DeviceIoControl(
                self._handle,
                FSCTL_ENUM_USN_DATA,
                ctypes.byref(med),
                ctypes.sizeof(med),
                buf,
                self.buffer_size,
                ctypes.byref(returned),
                None,
            )
            self.calls += 1
            if not ok:
                code = ctypes.get_last_error()
                self.last_error = code
                if code == ERROR_HANDLE_EOF:
                    return  # 正常结束
                raise UsnError(
                    "FSCTL_ENUM_USN_DATA 失败（{0}: {1}）".format(code, _winerror_text(code))
                )
            if returned.value <= 8:
                return
            batch = parse_batch(buf.raw[: returned.value])
            self.records_skipped += batch.skipped
            for record in batch.records:
                self.records_seen += 1
                yielded += 1
                yield record
                if limit is not None and yielded >= limit:
                    return
            med.StartFileReferenceNumber = batch.cursor
            if batch.cursor == 0:
                return  # 游标未定义，避免死循环
        # pragma: no cover - 循环内 return 结束

    # ── 增量读取 ─────────────────────────────────────

    def read_journal(
        self,
        start_usn: int,
        *,
        journal_id: Optional[int] = None,
        reason_mask: int = 0xFFFFFFFF,
        return_only_on_close: bool = False,
        timeout: int = 0,
        bytes_to_wait: int = 0,
        limit: Optional[int] = None,
    ) -> Iterator[UsnRecord]:
        """从 ``start_usn`` 起读取变更（``FSCTL_READ_USN_JOURNAL``）；无 journal 抛错。

        ``timeout`` / ``bytes_to_wait`` 均为 0 表示「有多少读多少，立即返回」，
        适合增量同步的「拉取当前积压」语义（不做无谓阻塞等待）。
        """
        info = self.query_journal()
        if info is None:
            raise JournalNotActiveError(
                "卷 {0} 未启用 USN journal，无法增量同步（本仓不主动创建）".format(self.volume)
            )
        data_in = _READ_USN_JOURNAL_DATA_V0(
            StartUsn=int(start_usn),
            ReasonMask=reason_mask,
            ReturnOnlyOnClose=1 if return_only_on_close else 0,
            Timeout=int(timeout),
            BytesToWaitFor=int(bytes_to_wait),
            UsnJournalID=info.journal_id if journal_id is None else int(journal_id),
        )
        buf = ctypes.create_string_buffer(self.buffer_size)
        returned = ctypes.c_uint32(0)
        yielded = 0
        cursor = int(start_usn)
        while True:
            ok = self._k32.DeviceIoControl(
                self._handle,
                FSCTL_READ_USN_JOURNAL,
                ctypes.byref(data_in),
                ctypes.sizeof(data_in),
                buf,
                self.buffer_size,
                ctypes.byref(returned),
                None,
            )
            self.calls += 1
            if not ok:
                code = ctypes.get_last_error()
                self.last_error = code
                raise UsnError(
                    "FSCTL_READ_USN_JOURNAL 失败（{0}: {1}）".format(code, _winerror_text(code))
                )
            if returned.value <= 8:
                return  # 无新记录
            batch = parse_batch(buf.raw[: returned.value])
            self.records_skipped += batch.skipped
            for record in batch.records:
                self.records_seen += 1
                yielded += 1
                yield record
                if limit is not None and yielded >= limit:
                    return
            if batch.cursor <= cursor:
                return  # 游标未前进，避免死循环
            cursor = batch.cursor
            data_in.StartUsn = cursor

    # ── 卷能力查询 ───────────────────────────────────

    def is_ntfs(self) -> bool:
        """该卷是否 NTFS（USN 的前提）。"""
        self.open()
        return is_ntfs_volume(self.volume)

    def __repr__(self) -> str:
        return "<UsnVolume {0}{1}>".format(
            self.volume, " opened" if self.opened else " closed"
        )


# ── 卷枚举辅助 ──────────────────────────────────────────────────────────


def is_ntfs_volume(volume: str) -> bool:
    """卷是否为 NTFS（``GetVolumeInformationW`` 的**文件系统名**参数，非卷标）。"""
    k32 = _kernel32()
    k32.GetVolumeInformationW.restype = ctypes.c_int
    k32.GetVolumeInformationW.argtypes = [
        ctypes.c_wchar_p,  # lpRootPathName
        ctypes.c_wchar_p,  # lpVolumeNameBuffer（卷标）
        ctypes.c_uint32,   # nVolumeNameSize
        ctypes.POINTER(ctypes.c_uint32),  # lpVolumeSerialNumber
        ctypes.POINTER(ctypes.c_uint32),  # lpMaximumComponentLength
        ctypes.POINTER(ctypes.c_uint32),  # lpFileSystemFlags
        ctypes.c_wchar_p,  # lpFileSystemNameBuffer（文件系统名）
        ctypes.c_uint32,   # nFileSystemNameSize
    ]
    label = ctypes.create_unicode_buffer(261)
    fs_name = ctypes.create_unicode_buffer(261)
    root = normalize_volume(volume) + "\\"
    ok = k32.GetVolumeInformationW(
        root, label, 261, None, None, None, fs_name, 261
    )
    return bool(ok) and fs_name.value.upper() == "NTFS"


def list_ntfs_volumes() -> List[str]:
    """列出本机 NTFS 盘符（``['C:', 'D:', ...]`` 形式，已按字母序）。"""
    k32 = _kernel32()
    k32.GetLogicalDrives.restype = ctypes.c_uint32
    k32.GetLogicalDrives.argtypes = []
    mask = k32.GetLogicalDrives()
    volumes: List[str] = []
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        letter = "{0}:".format(chr(ord("A") + i))
        try:
            if is_ntfs_volume(letter):
                volumes.append(letter)
        except Exception:
            continue
    return volumes
