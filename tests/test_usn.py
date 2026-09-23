"""USN 客户端测试：纯解析（跨平台可跑）+ 卷能力（Windows 限定）+ 实盘探针（开关启用）。"""

from __future__ import annotations

import os
import struct
import sys
import unittest
from unittest import mock

from index import usn
from index.usn import (
    FRN_INDEX_MASK,
    MAX_USN,
    MFT_ROOT_INDEX,
    JournalInfo,
    UsnError,
    UsnRecord,
    VolumeAccessError,
    describe_reason,
    device_path,
    normalize_volume,
    parse_batch,
    parse_usn_record,
)

LIVE = os.environ.get("FOOLPROOF_USN_LIVE") == "1"


# ── 合成记录构造（不依赖真实卷）────────────────────────────────────────


def build_v2(
    name: str,
    *,
    frn: int = 200,
    parent: int = 100,
    reason: int = 0,
    attributes: int = 0,
    usn: int = 12345,
    timestamp: int = 0,
) -> bytes:
    name_bytes = name.encode("utf-16-le")
    header = struct.pack(
        "<IHHQQqqIIIIHH",
        60 + len(name_bytes),
        2,
        0,
        frn,
        parent,
        usn,
        timestamp,
        reason,
        0,
        0,
        attributes,
        len(name_bytes),
        60,
    )
    return header + name_bytes


def build_v3(
    name: str,
    *,
    frn: int = 200,
    parent: int = 100,
    reason: int = 0,
    attributes: int = 0,
) -> bytes:
    name_bytes = name.encode("utf-16-le")
    header = struct.pack("<IHH", 76 + len(name_bytes), 3, 0)
    header += frn.to_bytes(16, "little") + parent.to_bytes(16, "little")
    header += struct.pack(
        "<qqIIIIHH", 999, 0, reason, 0, 0, attributes, len(name_bytes), 76
    )
    return header + name_bytes


def build_batch(records, cursor: int = 0) -> bytes:
    return struct.pack("<Q", cursor) + b"".join(records)


class ParseRecordTest(unittest.TestCase):
    def test_parse_v2_fields(self):
        record, nxt = parse_usn_record(build_v2("python.exe", frn=0x2000000000123, parent=0x2000000000005))
        self.assertIsNotNone(record)
        self.assertEqual(record.name, "python.exe")
        self.assertEqual(record.index, 0x123)  # 低 48 位 = MFT 记录号
        self.assertEqual(record.parent_index, MFT_ROOT_INDEX)
        self.assertEqual(record.usn, 12345)
        self.assertEqual(record.major, 2)
        self.assertFalse(record.is_directory)
        self.assertEqual(nxt, 60 + len("python.exe".encode("utf-16-le")))

    def test_parse_v2_flags(self):
        record, _ = parse_usn_record(
            build_v2("gone.txt", reason=usn.REASON_FILE_DELETE, attributes=usn.FILE_ATTRIBUTE_DIRECTORY)
        )
        self.assertTrue(record.deleted)
        self.assertTrue(record.is_directory)
        self.assertFalse(record.renamed)

        renamed, _ = parse_usn_record(build_v2("new.txt", reason=usn.REASON_RENAME_NEW_NAME))
        self.assertTrue(renamed.renamed)
        self.assertFalse(renamed.deleted)

    def test_parse_v3_uses_128bit_frn(self):
        # V3 的 FRN 为 128 位；序列号在 48 位以上，记录号取低 48 位。
        record, nxt = parse_usn_record(
            build_v3("site-packages", frn=0x0005_0000_0000_0042, parent=7)
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.major, 3)
        self.assertEqual(record.index, 0x42)
        self.assertEqual(record.parent_index, 7)
        self.assertEqual(record.name, "site-packages")
        self.assertEqual(nxt, 76 + len("site-packages".encode("utf-16-le")))

    def test_truncated_and_unsupported_are_reported_not_raised(self):
        truncated = build_v2("abc")[:30]
        record, nxt = parse_usn_record(truncated)
        self.assertIsNone(record)
        self.assertEqual(nxt, len(truncated))

        unsupported = bytearray(build_v2("abc"))
        struct.pack_into("<H", unsupported, 4, 4)  # MajorVersion = 4（V4 Extent，本仓不解析）
        record, nxt = parse_usn_record(bytes(unsupported))
        self.assertIsNone(record)
        self.assertGreater(nxt, 0)

    def test_non_ascii_name(self):
        record, _ = parse_usn_record(build_v2("失败日志.txt"))
        self.assertEqual(record.name, "失败日志.txt")


class ParseBatchTest(unittest.TestCase):
    def test_parses_cursor_and_records(self):
        batch = parse_batch(build_batch([build_v2("a.txt"), build_v2("b.txt", frn=201)], cursor=987))
        self.assertEqual(batch.cursor, 987)
        self.assertEqual([r.name for r in batch.records], ["a.txt", "b.txt"])
        self.assertEqual(batch.skipped, 0)
        self.assertTrue(batch.complete)
        self.assertEqual(len(batch), 2)

    def test_truncated_tail_marks_incomplete(self):
        data = build_batch([build_v2("a.txt"), build_v2("b.txt")])
        batch = parse_batch(data[: len(data) - 10])
        self.assertEqual([r.name for r in batch.records], ["a.txt"])
        self.assertEqual(batch.skipped, 1)
        self.assertFalse(batch.complete)

    def test_short_buffer(self):
        batch = parse_batch(b"\x01\x02")
        self.assertEqual(batch.records, [])
        self.assertFalse(batch.complete)

    def test_empty_batch(self):
        batch = parse_batch(struct.pack("<Q", 42))
        self.assertEqual(batch.cursor, 42)
        self.assertEqual(len(batch), 0)


class ReasonTest(unittest.TestCase):
    def test_describe_reason_known_bits(self):
        names = describe_reason(usn.REASON_FILE_CREATE | usn.REASON_RENAME_NEW_NAME)
        self.assertEqual(names, ["FILE_CREATE", "FILE_RENAME_NEW_NAME"])

    def test_describe_reason_unknown_bit_is_reported(self):
        names = describe_reason(0x40000000)
        self.assertEqual(names, ["0x40000000"])

    def test_describe_reason_empty(self):
        self.assertEqual(describe_reason(0), [])


class VolumePathTest(unittest.TestCase):
    def test_normalize_variants(self):
        for raw in ("G:", "G:\\", "g:", "\\\\.\\G:", "\\\\.\\g:"):
            self.assertEqual(normalize_volume(raw), "G:")
        self.assertEqual(normalize_volume(" E: "), "E:")

    def test_normalize_rejects_empty(self):
        for bad in ("", "  ", None, 7):
            with self.assertRaises(UsnError):
                normalize_volume(bad)

    def test_device_path(self):
        self.assertEqual(device_path("G:"), "\\\\.\\G:")
        self.assertEqual(device_path("\\\\.\\d:\\"), "\\\\.\\D:")


class ErrorHandlingTest(unittest.TestCase):
    def test_volume_access_error_explains_privilege(self):
        exc = VolumeAccessError("C:", 5)
        self.assertEqual(exc.volume, "C:")
        self.assertEqual(exc.code, 5)
        self.assertIn("拒绝访问", str(exc))
        self.assertIn("管理员", str(exc))

    def test_unknown_error_code_still_readable(self):
        message = usn._winerror_text(4321)
        self.assertTrue(message)

    def test_kernel32_rejects_non_windows(self):
        with mock.patch.object(usn.sys, "platform", "linux"):
            with self.assertRaises(UsnError) as cm:
                usn._kernel32()
        self.assertIn("仅支持 Windows", str(cm.exception))

    def test_journal_span_math(self):
        info = JournalInfo(1, 1000, 3000, 0, MAX_USN, 0, 0)
        self.assertEqual(info.journal_span, 2000)

    def test_read_journal_without_journal_fails_loud(self):
        """无 journal 的卷：增量必须显式报错（本仓不主动创建 journal），不得静默空转。"""
        vol = usn.UsnVolume("G:")
        with mock.patch.object(vol, "query_journal", return_value=None):
            with self.assertRaises(usn.JournalNotActiveError) as cm:
                list(vol.read_journal(0))
        self.assertIn("未启用 USN journal", str(cm.exception))


@unittest.skipUnless(sys.platform == "win32", "仅 Windows 有盘符语义")
class WindowsVolumeTest(unittest.TestCase):
    def test_is_ntfs_volume_returns_bool(self):
        self.assertIsInstance(usn.is_ntfs_volume("C:"), bool)

    def test_list_ntfs_volumes_shape(self):
        volumes = usn.list_ntfs_volumes()
        self.assertIsInstance(volumes, list)
        for volume in volumes:
            self.assertEqual(len(volume), 2)
            self.assertTrue(volume.endswith(":"))


@unittest.skipUnless(LIVE and sys.platform == "win32", "需 FOOLPROOF_USN_LIVE=1 且 Windows")
class LiveVolumeTest(unittest.TestCase):
    """实盘冒烟：非提权数据盘应可枚举；系统盘拒绝时应给明确错误（不是静默）。"""

    @classmethod
    def setUpClass(cls):
        cls.volumes = usn.list_ntfs_volumes()

    def test_enumerate_first_writable_volume(self):
        from index.paths import PathResolver

        target = next((v for v in self.volumes if v != "C:"), None)
        if target is None:
            self.skipTest("无数据盘可测")
        with usn.UsnVolume(target) as vol:
            info = vol.query_journal()
            self.assertIsNotNone(info)
            resolver = PathResolver(target)
            seen = 0
            for record in vol.enumerate_mft(limit=5000):
                resolver.add(record)
                seen += 1
            self.assertGreater(seen, 100)
            self.assertGreater(len(resolver), 100)
            resolved = [p for p in (resolver.resolve(n.index) for n in resolver.nodes()) if p]
            self.assertTrue(resolved)

    def test_system_drive_denied_or_readable_but_never_silent(self):
        try:
            with usn.UsnVolume("C:") as vol:
                self.assertTrue(vol.opened)
        except VolumeAccessError as exc:
            self.assertIn("管理员", str(exc))
            self.assertEqual(exc.code, 5)

    def test_enumerate_respects_deadline(self):
        import time as _time

        target = next((v for v in self.volumes if v != "C:"), None)
        if target is None:
            self.skipTest("无数据盘可测")
        with usn.UsnVolume(target) as vol:
            started = _time.monotonic()
            count = sum(1 for _ in vol.enumerate_mft(deadline=started + 1.5))
        self.assertLess(_time.monotonic() - started, 8.0)
        self.assertGreater(count, 0)

    def test_read_journal_backlog_and_catch_up(self):
        """增量通道实盘冒烟：能读到积压，从 NextUsn 起读不阻塞、不返回历史记录。"""
        target = next((v for v in self.volumes if v != "C:"), None)
        if target is None:
            self.skipTest("无数据盘可测")
        with usn.UsnVolume(target) as vol:
            info = vol.query_journal()
            self.assertIsNotNone(info)
            backlog = list(vol.read_journal(info.first_usn, limit=200))
            self.assertTrue(backlog)
            for record in backlog:
                self.assertTrue(record.name)
                self.assertGreaterEqual(record.usn, info.first_usn)
            nothing = list(vol.read_journal(info.next_usn, limit=50))
            for record in nothing:  # 期间可能有其它进程写入，故只校验边界
                self.assertGreaterEqual(record.usn, info.next_usn)
            self.assertLessEqual(len(nothing), 50)


if __name__ == "__main__":
    unittest.main()
