"""阶段 C 面板模型测试：任务日志过滤/统计、环境发现行与索引状态（无 tkinter、无盘）。"""

from __future__ import annotations

import unittest

from foolproof_operation.panels import EnvPanelModel, EnvRow, TaskLogModel, TaskLogRow


class FakeTaskLog:
    """假任务日志服务（含 log.clear）。"""

    def __init__(self, events=None):
        self._events = list(events or [])
        self.cleared = 0
        self.log = self

    def events(self, limit=None):
        return list(self._events)

    def summary(self):
        failures = sum(1 for e in self._events if e.get("failed") or e.get("ok") is False)
        return {
            "total": len(self._events),
            "events": {"install.done": 1, "check.done": 1} if self._events else {},
            "failures": failures,
            "first": self._events[0]["ts"] if self._events else None,
            "last": self._events[-1]["ts"] if self._events else None,
        }

    def clear(self):
        self.cleared += 1
        self._events = []


EVENTS = [
    {"ts": "2026-09-23 10:00:00", "event": "install.start", "count": 3, "upgrade": True},
    {"ts": "2026-09-23 10:00:05", "event": "install.done", "count": 3, "rc": 0, "ok": True},
    {"ts": "2026-09-23 10:01:00", "event": "check.done", "count": 24, "ok": False},
    {"ts": "2026-09-23 10:02:00", "event": "envs.done", "count": 208, "failed": 2},
]


class TaskLogModelTest(unittest.TestCase):
    def setUp(self):
        self.model = TaskLogModel(FakeTaskLog(EVENTS))

    def test_rows_shape_and_detail(self):
        rows = self.model.rows()
        self.assertEqual(len(rows), 4)
        self.assertIsInstance(rows[0], TaskLogRow)
        self.assertEqual(rows[0].event, "install.start")
        self.assertEqual(rows[0].detail, "count=3  upgrade=True")
        self.assertEqual(rows[0].as_tuple()[0], "2026-09-23 10:00:00")

    def test_failure_flag_from_ok_and_failed(self):
        flags = [row.failed for row in self.model.rows()]
        self.assertEqual(flags, [False, False, True, True])

    def test_only_failures_filter(self):
        rows = self.model.rows(only_failures=True)
        self.assertEqual([row.event for row in rows], ["check.done", "envs.done"])

    def test_event_filter_is_case_insensitive_substring(self):
        self.assertEqual([r.event for r in self.model.rows(event_filter="INSTALL")],
                         ["install.start", "install.done"])
        self.assertEqual([r.event for r in self.model.rows(event_filter="envs")], ["envs.done"])
        self.assertEqual(self.model.rows(event_filter="nope"), [])

    def test_limit_keeps_last_rows(self):
        rows = self.model.rows(limit=2)
        self.assertEqual([r.event for r in rows], ["check.done", "envs.done"])

    def test_header_and_event_names(self):
        header = self.model.header()
        self.assertIn("共 4 条", header)
        self.assertIn("失败计数 2", header)
        self.assertEqual(self.model.event_names(), ["check.done", "install.done"])

    def test_header_when_empty(self):
        model = TaskLogModel(FakeTaskLog([]))
        self.assertIn("暂无记录", model.header())

    def test_clear_delegates(self):
        service = FakeTaskLog(EVENTS)
        TaskLogModel(service).clear()
        self.assertEqual(service.cleared, 1)

    def test_non_dict_entries_are_skipped(self):
        model = TaskLogModel(FakeTaskLog([{"event": "ok"}, "garbage", None]))
        self.assertEqual([r.event for r in model.rows()], ["ok"])


class FakeEnv:
    def __init__(self, kind="venv", root="G:/x/.venv", version="3.13.14", python_exe="G:/x/.venv/Scripts/python.exe", valid=None):
        self.kind = kind
        self.root = root
        self.version = version
        self.python_exe = python_exe
        self.valid = valid


class FakeResult:
    def __init__(self, backend="index", attempts=None, missing=None):
        self.backend = backend
        self.attempts = attempts or [("index", "命中 811 条")]
        self.missing_volumes = missing or []


class FakeScanner:
    def __init__(self, envs=None, result=None, error=None):
        self._envs = envs if envs is not None else [FakeEnv(), FakeEnv(kind="standalone", version=None)]
        self.last_result = result if result is not None else FakeResult()
        self.calls = []
        self._error = error

    def discover(self, backend="auto", roots=None, deep=False):
        self.calls.append((backend, roots, deep))
        if self._error:
            raise self._error
        return list(self._envs)


class FakeIndex:
    def __init__(self, status=None, script=None):
        self._status = status if status is not None else {
            "G:": {"status": "ready", "detail": "重建：journal 已重建"},
            "C:": {"status": "denied", "detail": "拒绝访问"},
            "D:": {"status": "missing", "detail": "尚无索引"},
        }
        self.calls = []
        self._script = script or {}

    def status(self, **kwargs):
        return dict(self._status)

    def build(self, volume, force=False, **kwargs):
        self.calls.append(("build", volume, force))
        return {"volume": volume, "status": "ready", "records": 10, "seconds": 1.0}

    def sync(self, volume, **kwargs):
        self.calls.append(("sync", volume))
        return {"volume": volume, "status": "ready", "records": 10}


class EnvPanelModelTest(unittest.TestCase):
    def test_discover_rows_and_source(self):
        scanner = FakeScanner()
        model = EnvPanelModel(scanner, FakeIndex())
        rows = model.discover(backend="index", volumes=["G:"], deep=True)
        self.assertEqual(len(rows), 2)
        self.assertIsInstance(rows[0], EnvRow)
        self.assertEqual(rows[0].kind, "venv")
        self.assertEqual(rows[0].backend, "index")
        self.assertEqual(rows[0].version, "3.13.14")
        self.assertEqual(rows[1].version, "?")  # 版本缺失回落 "?"
        self.assertTrue(rows[1].python_exe.endswith("python.exe"))
        self.assertEqual(scanner.calls[0], ("index", ["G:"], True))

    def test_discover_header_lists_attempts_and_missing(self):
        scanner = FakeScanner(
            result=FakeResult(
                backend="walk",
                attempts=[("index", "不可用：尚无已建成的索引"), ("walk", "命中 4 条")],
                missing=["E:"],
            )
        )
        header = EnvPanelModel(scanner, None).discover_header()
        self.assertIn("来源后端：walk", header)
        self.assertIn("index: 不可用", header)
        self.assertIn("缺索引的卷：E:", header)

    def test_header_without_result(self):
        class NoResult:
            last_result = None

        class S:
            last_result = None

            def discover(self, **kw):
                return []

        model = EnvPanelModel(S(), None)
        self.assertIn("legacy", model.discover_header())

    def test_index_status_and_summary(self):
        model = EnvPanelModel(FakeScanner(), FakeIndex())
        rows = model.index_status()
        self.assertEqual([r[0] for r in rows], ["C:", "D:", "G:"])  # 已按卷名排序
        summary = model.index_summary()
        self.assertIn("已建索引：G:", summary)
        self.assertIn("未建：D:", summary)
        self.assertIn("被拒：C:", summary)

    def test_index_missing_service(self):
        model = EnvPanelModel(FakeScanner(), None)
        self.assertEqual(model.index_status(), [])
        self.assertIn("未装配", model.index_summary())
        with self.assertRaises(RuntimeError):
            model.build("G:")

    def test_build_and_sync_default_to_worker(self):
        index = FakeIndex()
        started = []
        model = EnvPanelModel(FakeScanner(), index, worker=lambda fn, *a, **kw: started.append((fn, a, kw)))
        model.build("G:", force=True)
        model.sync("G:")
        self.assertEqual(len(started), 2)
        self.assertEqual(index.calls, [])  # 未同步执行（交 worker）
        self.assertEqual(started[0][1], ("G:",))
        self.assertTrue(started[0][2]["force"])

    def test_build_and_sync_sync_mode(self):
        index = FakeIndex()
        model = EnvPanelModel(FakeScanner(), index)
        self.assertEqual(model.build("G:", async_run=False)["status"], "ready")
        self.assertEqual(model.sync("G:", async_run=False)["status"], "ready")
        self.assertEqual([c[0] for c in index.calls], ["build", "sync"])

    def test_discover_propagates_errors_for_panel_to_handle(self):
        model = EnvPanelModel(FakeScanner(error=RuntimeError("boom")), None)
        with self.assertRaises(RuntimeError):
            model.discover()


if __name__ == "__main__":
    unittest.main()
