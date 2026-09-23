"""基准探针测试：用假卷替换真实句柄，无盘、跨平台覆盖计数/样例/降级三条路径。"""

from __future__ import annotations

import unittest
from unittest import mock

from foolproof_operation.index import probe
from foolproof_operation.index.usn import JournalInfo, MAX_USN, UsnRecord, VolumeAccessError


def rec(index: int, name: str, parent: int, *, directory: bool = False) -> UsnRecord:
    return UsnRecord(
        frn=index,
        parent_frn=parent,
        usn=index,
        timestamp=0,
        reason=0,
        attributes=0x10 if directory else 0,
        name=name,
    )


TREE = [
    rec(5, ".", 5, directory=True),
    rec(100, "Python314", 5, directory=True),
    rec(101, "Lib", 100, directory=True),
    rec(200, "python.exe", 100),
    rec(201, "pyvenv.cfg", 100),
    rec(202, "notes.txt", 100),
]


class FakeVolume:
    """假卷：模拟 UsnVolume 的上下文管理器 + 枚举接口（不发任何 Win32 调用）。"""

    instances: list = []

    def __init__(self, volume, **kwargs):
        self.volume = volume
        self.records_seen = 0
        self.records_skipped = 0
        self.calls = 1
        self.closed = False
        FakeVolume.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return None

    def query_journal(self):
        return JournalInfo(journal_id=7, first_usn=1000, next_usn=3000, lowest_valid_usn=0,
                           max_usn=MAX_USN, maximum_size=0, allocation_delta=0)

    def enumerate_mft(self, *, limit=None, **kwargs):
        for i, record in enumerate(TREE):
            if limit is not None and i >= limit:
                return
            self.records_seen += 1
            yield record


class DeniedVolume(FakeVolume):
    def __enter__(self):
        raise VolumeAccessError("C:", 5)


class ProbeVolumeTest(unittest.TestCase):
    def setUp(self):
        FakeVolume.instances = []

    def test_counts_hits_directories_and_resolves_samples(self):
        with mock.patch.object(probe, "UsnVolume", FakeVolume):
            result = probe.probe_volume("G:", sample_paths=2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["records"], len(TREE))
        self.assertEqual(result["directories"], 3)  # Python314 / Lib /（根记录也被计为目录）
        self.assertEqual(result["hits"]["python.exe"], 1)
        self.assertEqual(result["hits"]["pyvenv.cfg"], 1)
        self.assertEqual(result["hits"]["conda-meta"], 0)
        self.assertEqual(result["samples"]["python.exe"], ["G:\\Python314\\python.exe"])
        self.assertEqual(result["samples"]["pyvenv.cfg"], ["G:\\Python314\\pyvenv.cfg"])
        self.assertEqual(result["journal_span"], 2000)
        self.assertFalse(result["truncated"])
        self.assertTrue(FakeVolume.instances[-1].closed)

    def test_truncation_flag_and_memory_free_mode(self):
        with mock.patch.object(probe, "UsnVolume", FakeVolume):
            truncated = probe.probe_volume("G:", max_records=3, sample_paths=2)
            flat = probe.probe_volume("G:", max_records=3, sample_paths=0)
        self.assertTrue(truncated["truncated"])
        self.assertEqual(truncated["records"], 3)
        self.assertFalse(flat["resolved_samples"])
        self.assertNotIn("index_nodes", flat)
        self.assertEqual(flat["hits"]["python.exe"], 0)  # 只扫到前 3 条（根/目录/目录）

    def test_access_denied_is_reported_not_raised(self):
        with mock.patch.object(probe, "UsnVolume", DeniedVolume):
            result = probe.probe_volume("C:")
        self.assertEqual(result["status"], "拒绝访问")
        self.assertIn("管理员", result["error"])
        self.assertEqual(result["records"], 0)


class FormatRowTest(unittest.TestCase):
    def test_error_row(self):
        row = probe._format_row(
            {"volume": "C:", "status": "拒绝访问", "error": "拒绝访问", "seconds": 0.1}
        )
        self.assertIn("| C: | 拒绝访问 |", row)
        self.assertIn("拒绝访问", row)

    def test_ok_row_shows_hits_and_truncation(self):
        row = probe._format_row(
            {
                "volume": "G:",
                "status": "ok",
                "journal_span": 12345,
                "records": 6789,
                "rate": 1234.0,
                "directories": 100,
                "truncated": True,
                "hits": {"python.exe": 3, "pyvenv.cfg": 0},
                "seconds": 1.5,
            }
        )
        self.assertIn("ok（截断）", row)
        self.assertIn("python=3", row)
        self.assertNotIn("pyvenv", row)  # 计数为 0 的命中不展示

    def test_ok_row_without_hits(self):
        row = probe._format_row(
            {
                "volume": "E:",
                "status": "ok",
                "journal_span": None,
                "records": 0,
                "rate": 0.0,
                "directories": 0,
                "truncated": False,
                "hits": {},
                "seconds": 0.2,
            }
        )
        self.assertIn("| - |", row)


class DefaultsTest(unittest.TestCase):
    def test_default_matches_cover_environment_evidence(self):
        for name in ("python.exe", "pyvenv.cfg", "conda-meta", "site-packages"):
            self.assertIn(name, probe.DEFAULT_MATCHES)


if __name__ == "__main__":
    unittest.main()
