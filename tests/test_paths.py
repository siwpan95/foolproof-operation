"""路径还原测试：父链回溯 / 断裂与成环 / rename 与 delete 增量语义 / 统计。"""

from __future__ import annotations

import unittest

from foolproof_operation.index.paths import PathResolver
from foolproof_operation.index.usn import (
    MFT_ROOT_INDEX,
    REASON_FILE_CREATE,
    REASON_FILE_DELETE,
    REASON_RENAME_NEW_NAME,
    REASON_RENAME_OLD_NAME,
    UsnRecord,
)


def rec(index: int, name: str, parent: int, *, directory: bool = False, reason: int = 0) -> UsnRecord:
    return UsnRecord(
        frn=index,
        parent_frn=parent,
        usn=index * 10,
        timestamp=0,
        reason=reason,
        attributes=0x10 if directory else 0,
        name=name,
    )


def build_tree(resolver: PathResolver) -> None:
    """G:\\Python314\\Lib\\site-packages 与其中的 python.exe。"""
    resolver.add(rec(MFT_ROOT_INDEX, ".", MFT_ROOT_INDEX, directory=True))
    resolver.add(rec(100, "Python314", MFT_ROOT_INDEX, directory=True))
    resolver.add(rec(101, "Lib", 100, directory=True))
    resolver.add(rec(102, "site-packages", 101, directory=True))
    resolver.add(rec(200, "python.exe", 100))
    resolver.add(rec(201, "pip", 102, directory=True))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver("G:")
        build_tree(self.r)

    def test_resolve_root(self):
        self.assertEqual(self.r.resolve(MFT_ROOT_INDEX), "G:\\")

    def test_resolve_file_and_dirs(self):
        self.assertEqual(self.r.resolve(200), "G:\\Python314\\python.exe")
        self.assertEqual(self.r.resolve(100), "G:\\Python314")
        self.assertEqual(self.r.resolve(102), "G:\\Python314\\Lib\\site-packages")
        self.assertEqual(self.r.resolve(201), "G:\\Python314\\Lib\\site-packages\\pip")

    def test_resolve_unknown_returns_none(self):
        self.assertIsNone(self.r.resolve(9999))

    def test_missing_parent_is_unresolved(self):
        r = PathResolver("G:")
        r.add(rec(300, "orphan.txt", 12345))
        self.assertIsNone(r.resolve(300))
        self.assertEqual(r.unresolved_count(), 1)

    def test_cycle_is_detected_not_hung(self):
        r = PathResolver("G:")
        r.add(rec(400, "a", 401, directory=True))
        r.add(rec(401, "b", 400, directory=True))
        self.assertIsNone(r.resolve(400))
        self.assertIsNone(r.resolve(401))
        self.assertEqual(r.stats().cycles, 2)  # 环上两个节点都被标记

    def test_relative_parts(self):
        self.assertEqual(self.r.relative_parts(200), ["Python314", "python.exe"])
        self.assertIsNone(self.r.relative_parts(9999))

    def test_stats(self):
        stats = self.r.stats()
        self.assertEqual(stats.nodes, 5)  # 根记录不入表（100/101/102/200/201）
        self.assertEqual(stats.directories, 4)
        self.assertEqual(stats.unresolved, 0)
        self.assertEqual(stats.cycles, 0)
        self.assertGreaterEqual(stats.max_depth, 3)

    def test_volume_label_required(self):
        with self.assertRaises(ValueError):
            PathResolver("")

    def test_volume_label_trailing_separator_normalized(self):
        self.assertEqual(PathResolver("G:\\").volume_label, "G:")

    def test_root_record_is_not_indexed(self):
        self.assertNotIn(MFT_ROOT_INDEX, self.r)
        self.assertEqual(len(self.r), 5)


class AddSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver("G:")

    def test_add_then_ignore_then_update(self):
        self.assertEqual(self.r.add(rec(100, "A", MFT_ROOT_INDEX, directory=True)), "add")
        self.assertEqual(self.r.add(rec(100, "A", MFT_ROOT_INDEX, directory=True)), "ignore")
        self.assertEqual(self.r.add(rec(100, "B", MFT_ROOT_INDEX, directory=True)), "update")
        self.assertEqual(self.r.resolve(100), "G:\\B")

    def test_add_ignores_empty_name(self):
        empty = UsnRecord(frn=1, parent_frn=5, usn=1, timestamp=0, reason=0, attributes=0, name="")
        self.assertEqual(self.r.add(empty), "ignore")
        self.assertEqual(len(self.r), 0)

    def test_directory_rename_reflected_in_children_without_cache(self):
        self.r.add(rec(100, "OldDir", MFT_ROOT_INDEX, directory=True))
        self.r.add(rec(200, "file.txt", 100))
        self.assertEqual(self.r.resolve(200), "G:\\OldDir\\file.txt")
        self.r.apply_change(rec(100, "NewDir", MFT_ROOT_INDEX, directory=True, reason=REASON_RENAME_NEW_NAME))
        self.assertEqual(self.r.resolve(200), "G:\\NewDir\\file.txt")


class ChangeSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver("G:")
        build_tree(self.r)

    def test_delete_removes_entry(self):
        self.assertEqual(self.r.apply_change(rec(200, "python.exe", 100, reason=REASON_FILE_DELETE)), "remove")
        self.assertIsNone(self.r.resolve(200))
        self.assertFalse(self.r.remove(200))  # 已不存在

    def test_rename_new_name_updates(self):
        action = self.r.apply_change(
            rec(200, "python3.exe", 100, reason=REASON_RENAME_NEW_NAME)
        )
        self.assertEqual(action, "update")
        self.assertEqual(self.r.resolve(200), "G:\\Python314\\python3.exe")

    def test_rename_old_name_is_ignored(self):
        action = self.r.apply_change(rec(200, "python.exe", 100, reason=REASON_RENAME_OLD_NAME))
        self.assertEqual(action, "ignore")

    def test_create_adds(self):
        action = self.r.apply_change(rec(300, "new.txt", 100, reason=REASON_FILE_CREATE))
        self.assertEqual(action, "add")
        self.assertEqual(self.r.resolve(300), "G:\\Python314\\new.txt")

    def test_unrelated_change_does_not_touch_name(self):
        action = self.r.apply_change(rec(200, "python.exe", 100, reason=0x00000001))  # DATA_OVERWRITE
        self.assertEqual(action, "ignore")
        self.assertEqual(self.r.resolve(200), "G:\\Python314\\python.exe")

    def test_move_to_new_parent(self):
        self.r.add(rec(103, "Scripts", 100, directory=True))
        self.r.apply_change(rec(200, "python.exe", 103, reason=REASON_RENAME_NEW_NAME))
        self.assertEqual(self.r.resolve(200), "G:\\Python314\\Scripts\\python.exe")

    def test_apply_changes_counts(self):
        counts = self.r.apply_changes(
            iter(
                [
                    rec(300, "a.txt", 100, reason=REASON_FILE_CREATE),
                    rec(301, "b.txt", 100, reason=REASON_FILE_CREATE),
                    rec(200, "python.exe", 100, reason=REASON_FILE_DELETE),
                    rec(201, "pip", 102, directory=True, reason=REASON_RENAME_OLD_NAME),
                ]
            )
        )
        self.assertEqual(counts, {"add": 2, "update": 0, "remove": 1, "ignore": 1})

    def test_delete_then_reuse_index_behaves_as_overwrite(self):
        """MFT 记录号复用：删除后新文件占用同一记录号 → 索引表现为覆盖更新（不是陈旧数据）。"""
        self.r.apply_change(rec(200, "python.exe", 100, reason=REASON_FILE_DELETE))
        self.r.apply_change(rec(200, "other.exe", 101, reason=REASON_FILE_CREATE))
        self.assertEqual(self.r.resolve(200), "G:\\Python314\\Lib\\other.exe")


if __name__ == "__main__":
    unittest.main()
