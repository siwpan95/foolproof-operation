"""阶段 6 插件测试：任务日志（JSONL）+ 启动审计（doctor）——全部无盘、不改状态。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from foolproof_operation.core.audit import LEVEL_ERROR, LEVEL_OK, LEVEL_WARN, AuditItem, AuditService, format_report
from foolproof_operation.core.context import Context
from foolproof_operation.core.runtime import build_runtime
from foolproof_operation.core.tasklog import TaskLog, read_events, summarize, tasklog_plugin


class TaskLogTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-tasklog-")
        self.path = os.path.join(self.tmpdir, "logs", "tasks.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_read_roundtrip(self):
        log = TaskLog(self.path)
        entry = log.record("install.start", count=3, upgrade=True)
        self.assertEqual(entry["event"], "install.start")
        self.assertTrue(os.path.isfile(self.path))
        events = log.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["count"], 3)
        self.assertIn("ts", events[0])

    def test_disabled_log_writes_nothing(self):
        log = TaskLog(self.path, enabled=False)
        self.assertIsNone(log.record("x"))
        self.assertFalse(os.path.isfile(self.path))

    def test_limit_returns_last_n(self):
        log = TaskLog(self.path)
        for i in range(5):
            log.record("evt", i=i)
        self.assertEqual([e["i"] for e in log.events(limit=2)], [3, 4])

    def test_bad_lines_are_skipped(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"event": "ok"}\n')
            fh.write("not json\n")
            fh.write("\n")
            fh.write('{"event": "ok2"}\n')
        events = read_events(self.path)
        self.assertEqual([e["event"] for e in events], ["ok", "ok2"])

    def test_summary_counts_failures_both_ways(self):
        log = TaskLog(self.path)
        log.record("a", ok=True)
        log.record("b", ok=False)
        log.record("c", failed=2)
        summary = log.summary()
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["events"], {"a": 1, "b": 1, "c": 1})
        self.assertEqual(summary["failures"], 3)  # ok=False 记 1，failed=2 记 2
        self.assertIsNotNone(summary["first"])
        self.assertEqual(summary["latest"]["event"], "c")

    def test_summary_of_empty_log(self):
        summary = summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertIsNone(summary["first"])
        self.assertIsNone(summary["latest"])

    def test_clear_keeps_file(self):
        log = TaskLog(self.path)
        log.record("x")
        log.clear()
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(log.events(), [])

    def test_write_failure_is_swallowed(self):
        log = TaskLog(os.path.join(self.tmpdir, "a.txt", "impossible", "tasks.jsonl"))
        with mock.patch("builtins.open", side_effect=OSError("denied")):
            self.assertIsNone(log.record("x"))
        self.assertIsNotNone(log.last_error)

    def test_missing_file_reads_empty(self):
        self.assertEqual(read_events(os.path.join(self.tmpdir, "absent.jsonl")), [])

    def test_plugin_row_provides_service(self):
        ctx = Context()
        ctx.install(tasklog_plugin, {"path": self.path})
        holder = ctx.get("tasklog")
        holder.record("from-row")
        self.assertEqual(holder.events()[0]["event"], "from-row")


class _FakeCategories:
    name = "categories"

    def __init__(self, problems=None, stats=None):
        self._problems = problems or []
        self._stats = stats or {"categories": 24, "entries": 761, "auto_discovery": 3, "total_entries": 764}

    def audit(self):
        return list(self._problems)

    def stats(self):
        return dict(self._stats)


class _FakeIndex:
    name = "fileindex"

    def __init__(self, status=None):
        self._status = status if status is not None else {"G:": {"status": "ready"}}

    def status(self, **kwargs):
        return dict(self._status)


class _FakeSearch:
    name = "search"

    def __init__(self, rows=None):
        self._rows = rows if rows is not None else [("index", True, "ok"), ("walk", True, "ok")]

    def availability(self):
        return list(self._rows)


class AuditServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-audit-")
        self.manifest = os.path.join(self.tmpdir, "foolproof.composition.json")
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"plugins": [{"id": "a", "plugin": "x"}, {"id": "b", "plugin": "y"}]}))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _service(self, *, categories=None, index=None, search=None, tasklog=None):
        ctx = Context()
        ctx.provide("categories", categories or _FakeCategories())
        ctx.provide("fileindex", index or _FakeIndex())
        ctx.provide("search", search or _FakeSearch())
        if tasklog is not None:
            ctx.provide("tasklog", tasklog)
        ctx.install(AuditService, {"manifest": self.manifest})
        return ctx.get("audit"), ctx

    def test_all_green(self):
        log = TaskLog(os.path.join(self.tmpdir, "t.jsonl"))
        service, _ = self._service(tasklog=log)
        items = service.run()
        # 6 项：插件清单 / 目标解释器 / 分类清单 / 索引状态 / 检索后端 / 任务日志
        self.assertEqual(len(items), 6)
        self.assertTrue(all(item.ok for item in items), [i.as_dict() for i in items])
        self.assertFalse(service.failed(items))

    def test_manifest_missing_is_error(self):
        service, _ = self._service()
        service.manifest_path = os.path.join(self.tmpdir, "absent.json")
        item = service._check_manifest()
        self.assertEqual(item.level, LEVEL_ERROR)
        self.assertIn("不存在", item.detail)
        self.assertTrue(service.failed(service.run()))

    def test_manifest_bad_json_is_error(self):
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        service, _ = self._service()
        self.assertEqual(service._check_manifest().level, LEVEL_ERROR)

    def test_manifest_row_without_id_is_error(self):
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"plugins": [{"plugin": "x"}]}))
        service, _ = self._service()
        self.assertEqual(service._check_manifest().level, LEVEL_ERROR)

    def test_categories_problems_are_error(self):
        service, _ = self._service(categories=_FakeCategories(problems=["分类「x」为空"]))
        item = service._check_categories()
        self.assertEqual(item.level, LEVEL_ERROR)
        self.assertIn("为空", item.advice)

    def test_categories_missing_row_is_warn(self):
        ctx = Context()
        ctx.provide("fileindex", _FakeIndex())
        ctx.provide("search", _FakeSearch())
        ctx.install(AuditService, {"manifest": self.manifest})
        item = ctx.get("audit")._check_categories()
        self.assertEqual(item.level, LEVEL_WARN)

    def test_index_states(self):
        service, _ = self._service(index=_FakeIndex({"G:": {"status": "ready"}, "C:": {"status": "denied"}}))
        item = service._check_index()
        self.assertEqual(item.level, LEVEL_WARN)  # denied → 提示可用管理员运行
        self.assertIn("denied=C:", item.detail)

        service2, _ = self._service(index=_FakeIndex({"G:": {"status": "missing"}}))
        item2 = service2._check_index()
        self.assertIn("envs --build", item2.advice)

        service3, _ = self._service(index=_FakeIndex({}))
        self.assertEqual(service3._check_index().level, LEVEL_WARN)

    def test_backends_all_unavailable_is_error(self):
        service, _ = self._service(search=_FakeSearch([("index", False, "无索引"), ("walk", False, "x")]))
        item = service._check_backends()
        self.assertEqual(item.level, LEVEL_ERROR)

    def test_tasklog_failure_is_warn(self):
        log = mock.Mock()
        log.log = mock.Mock()
        log.log.record.return_value = None
        log.log.last_error = "denied"
        log.log.path = "X"
        service, _ = self._service(tasklog=log)
        item = service._check_tasklog()
        self.assertEqual(item.level, LEVEL_WARN)
        self.assertIn("denied", item.detail)

    def test_format_report(self):
        report = format_report(
            [
                AuditItem(LEVEL_OK, "A", "fine"),
                AuditItem(LEVEL_WARN, "B", "meh", "do X"),
                AuditItem(LEVEL_ERROR, "C", "bad"),
            ]
        )
        self.assertIn("[✓] A：fine", report)
        self.assertIn("[!] B：meh　→ do X", report)
        self.assertIn("[✗] C：bad", report)
        self.assertIn("1 项通过 / 1 项警告 / 1 项错误", report)

    def test_real_runtime_audit_passes(self):
        with build_runtime() as rt:
            items = rt.get("audit").run()
        self.assertTrue(all(item.ok for item in items), [i.as_dict() for i in items])


class CliOpsTest(unittest.TestCase):
    """CLI 接线：`tasks` / `doctor` 与「所有子命令 help 可解析」的 argparse 回归。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-opscli-")
        self.path = os.path.join(self.tmpdir, "tasks.jsonl")
        self._env = mock.patch.dict(os.environ, {"FOOLPROOF_TASKLOG": self.path})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tasks_json_and_clear(self):
        from foolproof_operation import cli
        from foolproof_operation.core.tasklog import TaskLog

        TaskLog(self.path).record("install.done", count=2, ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.run_cli(["tasks", "--json"])
            rc_clear = cli.run_cli(["tasks", "--clear"])
        self.assertEqual(rc, 0)
        self.assertEqual(rc_clear, 0)
        self.assertEqual(TaskLog(self.path).events(), [])

    def test_tasks_empty_log_is_ok(self):
        from foolproof_operation import cli
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run_cli(["tasks"]), 0)

    def test_doctor_returns_zero_when_healthy(self):
        from foolproof_operation import cli
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run_cli(["doctor", "--json"]), 0)

    def test_every_subcommand_help_parses(self):
        """argparse 会把 help 文本当格式串：含 % 的写法（如 %LOCALAPPDATA%）会直接崩。"""
        from foolproof_operation import cli
        parser = cli.build_parser()
        for name in ("list", "install", "uninstall", "check", "scan", "envs", "es", "tasks", "doctor"):
            with self.subTest(cmd=name):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm:
                        parser.parse_args([name, "--help"])
                self.assertEqual(cm.exception.code, 0)

    def test_envs_and_scan_parsers_still_intact(self):
        from foolproof_operation import cli
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["scan"]).backend, "walk")
        self.assertEqual(parser.parse_args(["envs"]).backend, "auto")
        self.assertFalse(parser.parse_args(["tasks"]).clear)


if __name__ == "__main__":
    unittest.main()
