"""索引服务测试：后台建库 / 增量 / 查询 / 降级纪律（用假卷替换真实句柄，无盘无权限依赖）。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

from foolproof_operation.index.service import (
    STATUS_BUILDING,
    STATUS_DENIED,
    STATUS_MISSING,
    STATUS_READY,
    STATUS_STALE,
    STATUS_UNSUPPORTED,
    IndexService,
    default_index_dir,
)
from foolproof_operation.index.usn import (
    MFT_ROOT_INDEX,
    REASON_FILE_CREATE,
    REASON_FILE_DELETE,
    JournalInfo,
    UsnRecord,
    VolumeAccessError,
)


def rec(index: int, name: str, parent: int, *, directory: bool = False, reason: int = 0) -> UsnRecord:
    return UsnRecord(
        frn=index,
        parent_frn=parent,
        usn=index,
        timestamp=0,
        reason=reason,
        attributes=0x10 if directory else 0,
        name=name,
    )


TREE = [
    rec(MFT_ROOT_INDEX, ".", MFT_ROOT_INDEX, directory=True),
    rec(100, "Python314", MFT_ROOT_INDEX, directory=True),
    rec(101, "Scripts", 100, directory=True),
    rec(200, "python.exe", 101),
    rec(201, "pythonw.exe", 101),
    rec(202, "pyvenv.cfg", 100),
]


def journal(journal_id: int = 7, first: int = 1000, next_usn: int = 3000) -> JournalInfo:
    return JournalInfo(
        journal_id=journal_id,
        first_usn=first,
        next_usn=next_usn,
        lowest_valid_usn=0,
        max_usn=0x7FFFFFFFFFFFFFFF,
        maximum_size=0,
        allocation_delta=0,
    )


class FakeVolume:
    """假卷：可配置 journal、变更集与拒绝访问。"""

    journal_info = journal()
    changes = []
    denied = False
    tree = TREE
    last_read_start = None
    instances = []

    def __init__(self, volume, **kwargs):
        self.volume = volume
        self.records_seen = 0
        self.records_skipped = 0
        FakeVolume.instances.append(self)

    def __enter__(self):
        if self.denied:
            raise VolumeAccessError(self.volume, 5)
        return self

    def __exit__(self, *exc):
        return None

    def query_journal(self):
        return self.journal_info

    def enumerate_mft(self, *, limit=None, should_stop=None, **kwargs):
        for i, record in enumerate(self.tree):
            if limit is not None and i >= limit:
                return
            if should_stop is not None and should_stop():
                return
            self.records_seen += 1
            yield record

    def read_journal(self, start_usn, **kwargs):
        FakeVolume.last_read_start = start_usn
        for record in self.changes:
            yield record


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        FakeVolume.journal_info = journal()
        FakeVolume.changes = []
        FakeVolume.denied = False
        FakeVolume.tree = TREE
        FakeVolume.instances = []
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-service-")
        self.patch = mock.patch("foolproof_operation.index.service.UsnVolume", FakeVolume)
        self.patch.start()
        self.service = IndexService(None, {"volumes": ["G:"], "index_dir": self.tmpdir})

    def tearDown(self):
        self.service.stop()
        self.patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class BuildTest(ServiceFixture):
    def test_build_creates_index_and_reports_ready(self):
        state = self.service.build("G:")
        self.assertEqual(state["status"], STATUS_READY)
        self.assertEqual(state["records"], len(TREE) - 1)
        self.assertTrue(os.path.isfile(self.service.db_path("G:")))

    def test_build_is_idempotent_when_up_to_date(self):
        self.service.build("G:")
        again = self.service.build("G:")
        self.assertEqual(again["status"], STATUS_READY)
        self.assertEqual(again["detail"], "索引已最新")

    def test_build_rebuilds_when_journal_recreated(self):
        self.service.build("G:")
        FakeVolume.journal_info = journal(journal_id=999, next_usn=4000)
        state = self.service.build("G:")
        self.assertEqual(state["status"], STATUS_READY)
        self.assertIn("重建", state["detail"])

    def test_force_rebuild(self):
        self.service.build("G:")
        state = self.service.build("G:", force=True)
        self.assertEqual(state["status"], STATUS_READY)
        self.assertEqual(state["records"], len(TREE) - 1)

    def test_denied_volume_reports_status_and_keeps_index(self):
        """降级纪律：卷打不开 → 状态 denied，**索引保留且仍可查询**，绝不删库。"""
        self.service.build("G:")
        FakeVolume.denied = True
        state = self.service.build("G:", force=True)
        self.assertEqual(state["status"], STATUS_DENIED)
        self.assertIn("管理员", state["detail"])
        found = self.service.find("python.exe")
        self.assertEqual(len(found), 1)  # 旧索引仍可用

    def test_unsupported_when_no_journal(self):
        FakeVolume.journal_info = None
        state = self.service.build("G:")
        self.assertEqual(state["status"], STATUS_READY)  # 无 journal 仍可全量建库
        self.assertEqual(state["records"], len(TREE) - 1)
        sync_state = self.service.sync("G:")
        self.assertEqual(sync_state["status"], STATUS_UNSUPPORTED)

    def test_max_records_caps_build(self):
        service = IndexService(None, {"volumes": ["G:"], "index_dir": self.tmpdir, "max_records": 3})
        state = service.build("G:")
        self.assertEqual(state["status"], STATUS_READY)
        # 上限作用于**枚举条数**（3 条），而根记录不入表 → 落库 2 行
        self.assertEqual(state["records"], 2)
        service.stop()


class QueryTest(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service.build("G:")

    def test_search_returns_resolved_paths(self):
        result = self.service.search(["python.exe", "pyvenv.cfg"])
        self.assertEqual(result["volumes"], ["G:"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(
            [hit["path"] for hit in result["hits"]["python.exe"]],
            ["G:\\Python314\\Scripts\\python.exe"],
        )
        self.assertEqual(result["hits"]["pyvenv.cfg"][0]["path"], "G:\\Python314\\pyvenv.cfg")

    def test_find_returns_paths(self):
        self.assertEqual(self.service.find("pythonw.exe"), ["G:\\Python314\\Scripts\\pythonw.exe"])
        self.assertEqual(self.service.find("absent.exe"), [])

    def test_search_without_resolve_returns_names(self):
        result = self.service.search(["python.exe"], resolve=False)
        self.assertEqual(result["hits"]["python.exe"][0]["path"], "python.exe")

    def test_missing_index_is_reported_not_silent(self):
        service = IndexService(None, {"volumes": ["G:", "E:"], "index_dir": self.tmpdir})
        service.build("G:")
        result = service.search(["python.exe"])
        self.assertEqual(result["missing"], ["E:"])
        self.assertIn("G:", result["volumes"])
        service.stop()

    def test_status_lists_volumes_with_state(self):
        status = self.service.status()
        self.assertEqual(status["G:"]["status"], STATUS_READY)
        self.assertEqual(status["G:"]["records"], len(TREE) - 1)


class SyncTest(ServiceFixture):
    def test_incremental_sync_applies_changes_and_advances_cursor(self):
        self.service.build("G:")
        FakeVolume.changes = [
            rec(300, "new.exe", 101, reason=REASON_FILE_CREATE),
            rec(201, "pythonw.exe", 101, reason=REASON_FILE_DELETE),
        ]
        FakeVolume.journal_info = journal(next_usn=3100)
        state = self.service.sync("G:")
        self.assertEqual(state["status"], STATUS_READY)
        self.assertEqual(state["counts"], {"upsert": 1, "remove": 1, "ignore": 0})
        store = self.service.store("G:")
        self.assertIsNotNone(store.get_node(300))
        self.assertIsNone(store.get_node(201))
        self.assertEqual(store.get_meta("next_usn"), "3100")
        self.assertEqual(FakeVolume.last_read_start, 3000)  # 从库里记的游标续读

    def test_sync_marks_stale_when_rebuild_needed(self):
        self.service.build("G:")
        FakeVolume.journal_info = journal(journal_id=999)
        state = self.service.sync("G:")
        self.assertEqual(state["status"], STATUS_STALE)
        self.assertIn("需重建", state["detail"])

    def test_sync_on_denied_volume_reports_denied(self):
        self.service.build("G:")
        FakeVolume.denied = True
        self.assertEqual(self.service.sync("G:")["status"], STATUS_DENIED)


class BackgroundTest(ServiceFixture):
    def test_ensure_index_background_completes(self):
        self.service.start()
        self.service.ensure_index("G:", background=True)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self.service.status().get("G:", {}).get("status") == STATUS_READY:
                break
            time.sleep(0.02)
        self.assertEqual(self.service.status()["G:"]["status"], STATUS_READY)
        self.assertEqual(self.service.find("python.exe"), ["G:\\Python314\\Scripts\\python.exe"])

    def test_auto_build_on_start(self):
        service = IndexService(
            None, {"volumes": ["G:"], "index_dir": self.tmpdir, "auto_build": True}
        )
        service.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if service.status().get("G:", {}).get("status") == STATUS_READY:
                break
            time.sleep(0.02)
        self.assertEqual(service.status()["G:"]["status"], STATUS_READY)
        service.stop()

    def test_stop_is_idempotent_and_closes_stores(self):
        self.service.start()
        self.service.build("G:")
        self.service.stop()
        self.service.stop()
        self.assertFalse(self.service._started)

    def test_worker_survives_volume_failure(self):
        service = IndexService(None, {"volumes": ["G:"], "index_dir": self.tmpdir})
        service.start()
        FakeVolume.denied = True
        service.ensure_index("G:", background=True)
        time.sleep(0.2)
        self.assertEqual(service.status()["G:"]["status"], STATUS_DENIED)
        service.stop()


class ServiceContractTest(unittest.TestCase):
    def test_is_a_kernel_service_with_expected_name(self):
        self.assertEqual(IndexService.name, "fileindex")
        self.assertEqual(IndexService.inject, ())

    def test_default_index_dir_is_outside_repo(self):
        path = default_index_dir()
        self.assertTrue(os.path.isabs(path))
        self.assertNotIn(os.sep + "repos" + os.sep, path)

    def test_db_path_naming(self):
        service = IndexService(None, {"volumes": ["G:"], "index_dir": tempfile.gettempdir()})
        self.assertTrue(service.db_path("g:").endswith("G.db"))

    def test_detected_volumes_prefers_config(self):
        service = IndexService(None, {"volumes": ["g:", "e:\\"]})
        self.assertEqual(service.detected_volumes(), ["G:", "E:"])


LIVE = os.environ.get("FOOLPROOF_USN_LIVE") == "1"


@unittest.skipUnless(LIVE and sys.platform == "win32", "需 FOOLPROOF_USN_LIVE=1 且 Windows")
class LiveIndexTest(unittest.TestCase):
    """实盘回归：真卷小样建库 + 真查询（默认跳过，避免把磁盘/权限依赖带进常规测试）。"""

    def setUp(self):
        from foolproof_operation.index.usn import list_ntfs_volumes

        self.volumes = [v for v in list_ntfs_volumes() if v != "C:"]
        if not self.volumes:
            self.skipTest("无数据盘可测")
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-live-index-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_and_query_real_volume(self):
        from foolproof_operation.index.service import STATUS_READY

        service = IndexService(
            None,
            {
                "volumes": self.volumes,
                "index_dir": self.tmpdir,
                "max_records": 200_000,
                "deadline_seconds": 60,
            },
        )
        try:
            state = service.build(self.volumes[0])
            self.assertEqual(state["status"], STATUS_READY)
            self.assertGreater(state["records"], 0)
            started = time.monotonic()
            found = service.find("python.exe")
            elapsed_ms = (time.monotonic() - started) * 1000
            self.assertTrue(found, "真卷上应能查到 python.exe")
            for path in found:
                self.assertTrue(path.startswith(self.volumes[0]), path)
            # 目标口径是 <100ms（阶段 3 验收）；单测留宽松上界以免受机器负载影响
            self.assertLess(elapsed_ms, 2000.0)
        finally:
            service.stop()


if __name__ == "__main__":
    unittest.main()
