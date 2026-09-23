"""索引库测试：建库/查询/路径还原/增量/重建判定（全部无盘，内存库或临时文件库）。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from index.store import IndexStore, NodeRow, StoreError
from index.usn import (
    MFT_ROOT_INDEX,
    REASON_FILE_CREATE,
    REASON_FILE_DELETE,
    REASON_RENAME_NEW_NAME,
    REASON_RENAME_OLD_NAME,
    JournalInfo,
    UsnRecord,
)
from index import store as store_module


def rec(index: int, name: str, parent: int, *, directory: bool = False, reason: int = 0) -> UsnRecord:
    return UsnRecord(
        frn=index,
        parent_frn=parent,
        usn=index * 7,
        timestamp=0,
        reason=reason,
        attributes=0x10 if directory else 0,
        name=name,
    )


TREE = [
    rec(MFT_ROOT_INDEX, ".", MFT_ROOT_INDEX, directory=True),
    rec(100, "Python314", MFT_ROOT_INDEX, directory=True),
    rec(101, "Scripts", 100, directory=True),
    rec(102, "Lib", 100, directory=True),
    rec(103, "site-packages", 102, directory=True),
    rec(200, "python.exe", 101),
    rec(201, "pythonw.exe", 101),
    rec(202, "pyvenv.cfg", 100),
    rec(203, "requests", 103, directory=True),
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


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-store-")
        self.path = os.path.join(self.tmpdir, "G.db")
        self.store = IndexStore.open("G:", self.path)
        self.store.begin_build()
        self.inserted = self.store.add_records(iter(TREE))
        self.store.finish_build(journal_id=7, next_usn=3000)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class BuildTest(StoreFixture):
    def test_records_exclude_root_and_empty_names(self):
        self.assertEqual(self.inserted, len(TREE) - 1)
        self.assertEqual(self.store.count(), len(TREE) - 1)
        self.assertIsNone(self.store.get_node(MFT_ROOT_INDEX))

    def test_directory_count(self):
        self.assertEqual(self.store.directory_count(), 5)  # Python314/Scripts/Lib/site-packages/requests

    def test_meta_and_stats(self):
        stats = self.store.stats()
        self.assertTrue(stats.complete)
        self.assertEqual(stats.journal_id, 7)
        self.assertEqual(stats.next_usn, 3000)
        self.assertEqual(stats.schema_version, store_module.SCHEMA_VERSION)
        self.assertIsNotNone(stats.built_at)
        self.assertGreater(stats.db_bytes, 0)

    def test_wal_mode_and_pragmas(self):
        mode = self.store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_interrupted_state(self):
        self.store.begin_build()
        self.assertTrue(self.store.interrupted)
        self.assertFalse(self.store.stats().complete)
        self.assertEqual(self.store.count(), 0)

    def test_abort_build(self):
        self.store.begin_build()
        self.store.add_records(iter(TREE))
        self.store.abort_build()
        self.assertEqual(self.store.count(), 0)
        self.assertTrue(self.store.interrupted)

    def test_small_batch_size_keeps_all_rows(self):
        with IndexStore.open("G:", os.path.join(self.tmpdir, "batch.db"), batch_size=2) as s:
            s.begin_build()
            s.add_records(iter(TREE))
            s.finish_build(journal_id=1, next_usn=1)
            self.assertEqual(s.count(), len(TREE) - 1)

    def test_hard_link_duplicate_collapses_to_last_name(self):
        """同一 FRN 多个名称（硬链接）折叠为最后一条 —— 已知取舍，见 store 模块 docstring。"""
        with IndexStore.open("G:", os.path.join(self.tmpdir, "hard.db")) as s:
            s.begin_build()
            s.add_records(iter([rec(300, "first.exe", 100), rec(300, "second.exe", 100)]))
            s.finish_build(journal_id=1, next_usn=1)
            self.assertEqual(s.count(), 1)
            self.assertEqual(s.get_node(300).name, "second.exe")


class QueryTest(StoreFixture):
    def test_resolve_path(self):
        self.assertEqual(self.store.resolve_path(200), "G:\\Python314\\Scripts\\python.exe")
        self.assertEqual(self.store.resolve_path(103), "G:\\Python314\\Lib\\site-packages")
        self.assertEqual(self.store.resolve_path(MFT_ROOT_INDEX), "G:\\")

    def test_resolve_unknown_and_broken_chain(self):
        self.assertIsNone(self.store.resolve_path(9999))
        with IndexStore.open("G:", os.path.join(self.tmpdir, "orphan.db")) as s:
            s.begin_build()
            s.add_records(iter([rec(400, "orphan.txt", 123456)]))
            s.finish_build(journal_id=1, next_usn=1)
            self.assertIsNone(s.resolve_path(400))

    def test_resolve_cycle_returns_none(self):
        with IndexStore.open("G:", os.path.join(self.tmpdir, "cycle.db")) as s:
            s.begin_build()
            s.add_records(
                iter([rec(500, "a", 501, directory=True), rec(501, "b", 500, directory=True)])
            )
            s.finish_build(journal_id=1, next_usn=1)
            self.assertIsNone(s.resolve_path(500))

    def test_find_by_name_is_case_insensitive(self):
        self.assertEqual([n.index for n in self.store.find_by_name("PYTHON.EXE")], [200])
        self.assertEqual(self.store.find_by_name("nope.exe"), [])

    def test_find_by_name_limit(self):
        self.assertEqual(len(self.store.find_by_name("python.exe", limit=1)), 1)

    def test_find_by_names_batch(self):
        found = self.store.find_by_names(["python.exe", "pythonw.exe", "pyvenv.cfg", "absent"])
        self.assertEqual([n.index for n in found["python.exe"]], [200])
        self.assertEqual([n.index for n in found["pythonw.exe"]], [201])
        self.assertEqual([n.index for n in found["pyvenv.cfg"]], [202])
        self.assertEqual(found["absent"], [])
        self.assertEqual(set(found), {"python.exe", "pythonw.exe", "pyvenv.cfg", "absent"})

    def test_find_by_names_dedup_and_limit(self):
        found = self.store.find_by_names(["python.exe", "python.exe"], limit_per_name=1)
        self.assertEqual(len(found["python.exe"]), 1)
        self.assertEqual(len(found), 1)

    def test_children(self):
        self.assertEqual(sorted(n.index for n in self.store.children(100)), [101, 102, 202])
        self.assertEqual(self.store.children(9999), [])
        self.assertEqual(len(self.store.children(101, limit=1)), 1)

    def test_resolve_paths_bulk(self):
        resolved = self.store.resolve_paths([200, 202, 9999])
        self.assertEqual(resolved[200], "G:\\Python314\\Scripts\\python.exe")
        self.assertEqual(resolved[202], "G:\\Python314\\pyvenv.cfg")
        self.assertIsNone(resolved[9999])

    def test_node_row_shape(self):
        node = self.store.get_node(103)
        self.assertIsInstance(node, NodeRow)
        self.assertTrue(node.is_directory)
        self.assertFalse(self.store.get_node(200).is_directory)
        self.assertEqual(node.parent, 102)


class QueryPlanTest(StoreFixture):
    """性能回归护栏：主查询必须命中索引，不得退化为全表扫描。

    实测教训（阶段 3）：``name IN (...) COLLATE NOCASE`` 会 ``SCAN nodes``
    （G: 卷 251 万行 355ms）；改为逐项 ``name = ? COLLATE NOCASE`` 的 OR 形式后
    命中 ``COLLATE NOCASE`` 索引（2.0ms）。本类把该结论钉在测试里。
    """

    def _plan(self, sql, params):
        return [row[-1] for row in self.store.connection.execute("EXPLAIN QUERY PLAN " + sql, params)]

    def test_name_lookup_uses_index_not_full_scan(self):
        plan = self._plan(store_module.name_lookup_sql(5), ["python.exe"] * 5)
        joined = " | ".join(plan)
        self.assertNotIn("SCAN nodes", joined)
        self.assertIn("idx_nodes_name_lower", joined)

    def test_single_name_lookup_uses_index(self):
        plan = self._plan(
            "SELECT frn_index FROM nodes WHERE name = ? COLLATE NOCASE", ["python.exe"]
        )
        self.assertNotIn("SCAN nodes", " | ".join(plan))

    def test_children_lookup_uses_parent_index(self):
        plan = self._plan("SELECT frn_index FROM nodes WHERE parent = ?", [100])
        self.assertNotIn("SCAN nodes", " | ".join(plan))

    def test_name_lookup_sql_rejects_zero(self):
        with self.assertRaises(ValueError):
            store_module.name_lookup_sql(0)


class IncrementalTest(StoreFixture):
    def test_add_update_remove_ignore(self):
        counts = self.store.apply_changes(
            iter(
                [
                    rec(300, "new.exe", 101, reason=REASON_FILE_CREATE),
                    rec(200, "python3.exe", 101, reason=REASON_RENAME_NEW_NAME),
                    rec(201, "pythonw.exe", 101, reason=REASON_FILE_DELETE),
                    rec(202, "pyvenv.cfg", 100, reason=REASON_RENAME_OLD_NAME),
                ]
            )
        )
        self.assertEqual(counts, {"upsert": 2, "remove": 1, "ignore": 1})
        self.assertIsNotNone(self.store.get_node(300))
        self.assertEqual(self.store.get_node(200).name, "python3.exe")
        self.assertIsNone(self.store.get_node(201))
        self.assertEqual(self.store.get_node(202).name, "pyvenv.cfg")

    def test_move_updates_parent_and_path(self):
        self.store.apply_changes(iter([rec(202, "pyvenv.cfg", 101, reason=REASON_RENAME_NEW_NAME)]))
        self.assertEqual(self.store.resolve_path(202), "G:\\Python314\\Scripts\\pyvenv.cfg")

    def test_root_and_empty_name_ignored(self):
        counts = self.store.apply_changes(
            iter(
                [
                    rec(MFT_ROOT_INDEX, ".", MFT_ROOT_INDEX),
                    UsnRecord(frn=9, parent_frn=5, usn=1, timestamp=0, reason=0, attributes=0, name=""),
                ]
            )
        )
        self.assertEqual(counts["ignore"], 2)

    def test_journal_state_advances(self):
        self.store.set_journal_state(journal_id=9, next_usn=4242)
        self.assertEqual(self.store.get_meta("journal_id"), "9")
        self.assertEqual(self.store.get_meta("next_usn"), "4242")


class RebuildDecisionTest(StoreFixture):
    def test_up_to_date(self):
        self.assertEqual(self.store.needs_rebuild(journal(next_usn=3000)), (False, ""))

    def test_journal_recreated(self):
        rebuild, reason = self.store.needs_rebuild(journal(journal_id=999))
        self.assertTrue(rebuild)
        self.assertIn("重建", reason)

    def test_cursor_behind_journal_window(self):
        rebuild, reason = self.store.needs_rebuild(journal(first=5000, next_usn=9000))
        self.assertTrue(rebuild)
        self.assertIn("窗口", reason)

    def test_cursor_ahead_is_anomaly(self):
        rebuild, reason = self.store.needs_rebuild(journal(next_usn=100))
        self.assertTrue(rebuild)
        self.assertIn("超前", reason)

    def test_missing_journal_state(self):
        with IndexStore.open("G:", os.path.join(self.tmpdir, "nojournal.db")) as s:
            s.begin_build()
            s.add_records(iter(TREE))
            s.finish_build()  # 完成建库但不写 journal 状态（模拟外部/旧版产物）
            self.assertFalse(s.interrupted)
            rebuild, reason = s.needs_rebuild(journal())
            self.assertTrue(rebuild)
            self.assertIn("journal 状态", reason)

    def test_interrupted_wins_over_everything(self):
        self.store.begin_build()
        rebuild, reason = self.store.needs_rebuild(journal())
        self.assertTrue(rebuild)
        self.assertIn("未完成", reason)

    def test_no_journal_volume_does_not_force_rebuild(self):
        self.assertEqual(self.store.needs_rebuild(None), (False, ""))

    def test_sync_lag(self):
        self.assertEqual(self.store.sync_lag(journal(next_usn=3100)), 100)
        self.assertEqual(self.store.sync_lag(journal(next_usn=2000)), 0)
        self.assertIsNone(self.store.sync_lag(None))


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-schema-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_schema_version_mismatch_requires_rebuild(self):
        path = os.path.join(self.tmpdir, "v.db")
        with IndexStore.open("G:", path) as s:
            s.set_meta("schema_version", str(store_module.SCHEMA_VERSION + 1))
        with self.assertRaises(StoreError) as cm:
            IndexStore.open("G:", path)
        self.assertIn("结构版本不匹配", str(cm.exception))
    def test_closed_store_rejects_use(self):
        s = IndexStore.open("G:", os.path.join(self.tmpdir, "closed.db"))
        s.close()
        with self.assertRaises(StoreError):
            s.connection

    def test_creates_parent_directory(self):
        path = os.path.join(self.tmpdir, "deep", "nested", "G.db")
        with IndexStore.open("G:", path) as s:
            s.begin_build()
            s.finish_build(journal_id=1, next_usn=1)
        self.assertTrue(os.path.isfile(path))

    def test_reopen_preserves_data(self):
        path = os.path.join(self.tmpdir, "reopen.db")
        with IndexStore.open("G:", path) as s:
            s.begin_build()
            s.add_records(iter(TREE))
            s.finish_build(journal_id=7, next_usn=3000)
        with IndexStore.open("G:", path) as s:
            self.assertEqual(s.count(), len(TREE) - 1)
            self.assertEqual(s.resolve_path(200), "G:\\Python314\\Scripts\\python.exe")
            self.assertEqual(s.needs_rebuild(journal()), (False, ""))


if __name__ == "__main__":
    unittest.main()
