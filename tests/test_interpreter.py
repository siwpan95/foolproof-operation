"""目标解释器解析与自指防护测试（冻结版事故的回归锁）。

事故现场（2026-09-23）：便携版里 ``sys.executable`` 是本程序自身，被当成 Python 使用后
每个 pip 操作都重新拉起本程序（argv 不匹配时开 GUI 且永不退出），实测连锁 22 个进程、
pip 检查永久挂死。下面的用例锁住解析优先级与「绝不 spawn 自身」。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from foolproof_operation.core.interpreter import (
    describe,
    is_self_executable,
    resolve_python_exe,
)
from foolproof_operation.plugins.pip import PipService


class InterpreterTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-interp-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _file(self, name):
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as fh:
            fh.write(b"MZ")
        return path

    # ── 自指判定 ─────────────────────────────────────

    def test_is_self_by_name(self):
        self.assertTrue(is_self_executable(self._file("requirements.exe")))
        self.assertTrue(is_self_executable(self._file("requirements-dbg.exe")))
        self.assertFalse(is_self_executable(self._file("python.exe")))
        self.assertFalse(is_self_executable(None))

    def test_is_self_when_frozen_matches_sys_executable(self):
        fake = self._file("weird-name.exe")
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", fake):
            self.assertTrue(is_self_executable(fake))
            self.assertFalse(is_self_executable(self._file("python.exe")))

    # ── 解析优先级 ───────────────────────────────────

    def test_source_run_uses_current_interpreter(self):
        path, source = resolve_python_exe(env={})
        self.assertEqual(path, sys.executable)
        self.assertIn("源码运行", source)

    def test_explicit_wins(self):
        target = self._file("python.exe")
        path, source = resolve_python_exe(target, env={})
        self.assertEqual(path, os.path.abspath(target))
        self.assertIn("配置", source)

    def test_explicit_self_is_rejected(self):
        path, source = resolve_python_exe(self._file("requirements.exe"), env={})
        self.assertIsNone(path)
        self.assertIn("本程序自身", source)

    def test_explicit_missing_is_rejected(self):
        path, source = resolve_python_exe(os.path.join(self.tmpdir, "nope.exe"), env={})
        self.assertIsNone(path)
        self.assertIn("不可用", source)

    def test_argv_python_flag(self):
        target = self._file("python.exe")
        path, source = resolve_python_exe(env={}, argv=["--python", target, "cli", "list"])
        self.assertEqual(path, os.path.abspath(target))
        self.assertIn("--python", source)
        path2, _ = resolve_python_exe(env={}, argv=["-p", target])
        self.assertEqual(path2, os.path.abspath(target))

    def test_argv_self_is_rejected(self):
        path, source = resolve_python_exe(env={}, argv=["--python", self._file("requirements.exe")])
        self.assertIsNone(path)
        self.assertIn("本程序自身", source)

    def test_env_override(self):
        target = self._file("python.exe")
        path, source = resolve_python_exe(env={"FOOLPROOF_PYTHON": target})
        self.assertEqual(path, os.path.abspath(target))
        self.assertIn("FOOLPROOF_PYTHON", source)
        path2, source2 = resolve_python_exe(env={"FOOLPROOF_PYTHON": os.path.join(self.tmpdir, "gone.exe")})
        self.assertIsNone(path2)
        self.assertIn("不可用", source2)

    def test_frozen_never_returns_self(self):
        """冻结版：**绝不**把本程序自身当解释器（本用例是事故的直接回归锁）。"""
        fake_self = self._file("requirements.exe")
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", fake_self):
            path, source = resolve_python_exe(env={})
        self.assertNotEqual(path, fake_self)
        if path is not None:  # 本机 PATH 上有 python 时会解析到它
            self.assertFalse(is_self_executable(path))

    def test_frozen_without_any_python_fails_loud(self):
        fake_self = self._file("requirements.exe")
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", fake_self), \
             mock.patch("shutil.which", return_value=None):
            path, source = resolve_python_exe(env={})
        self.assertIsNone(path)
        self.assertIn("--python", source)
        self.assertIn("FOOLPROOF_PYTHON", source)

    def test_describe(self):
        self.assertIn("来源", describe("C:/x/python.exe", "测试"))
        self.assertIn("未解析到", describe(None, "没有"))


class PipSelfSpawnGuardTest(unittest.TestCase):
    """服务层硬防护：即便有人传错解释器，也绝不 spawn 本程序自身。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-guard-")
        self.fake_self = os.path.join(self.tmpdir, "requirements.exe")
        with open(self.fake_self, "wb") as fh:
            fh.write(b"MZ")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_refuses_to_spawn_self(self):
        service = PipService(None, {"python_exe": self.fake_self})
        with mock.patch("subprocess.run") as run:
            rc, out, err = service.run_full([self.fake_self, "-m", "pip", "--version"])
        run.assert_not_called()
        self.assertEqual(rc, -1)
        self.assertIn("拒绝执行", err)
        self.assertIn("本程序自身", err)

    def test_refuses_empty_interpreter(self):
        service = PipService(None, {"python_exe": ""})
        with mock.patch("subprocess.run") as run:
            rc, _out, err = service.run_full(["", "-m", "pip", "--version"])
        run.assert_not_called()
        self.assertEqual(rc, -1)
        self.assertIn("未解析到目标 Python 解释器", err)

    def test_health_reports_refusal_instead_of_hanging(self):
        service = PipService(None, {"python_exe": self.fake_self})
        with mock.patch("subprocess.run") as run:
            health = service.health(self.fake_self)
        run.assert_not_called()
        self.assertEqual(health["state"], "missing")
        self.assertIn("本程序自身", health["detail"])


class DoctorInterpreterItemTest(unittest.TestCase):
    def test_item_reports_error_when_unresolved(self):
        from foolproof_operation.core.audit import LEVEL_ERROR, AuditService

        service = AuditService(None, {})
        with mock.patch(
            "foolproof_operation.core.interpreter.resolve_python_exe",
            return_value=(None, "冻结版未能找到真实 Python 解释器"),
        ):
            item = service._check_interpreter()
        self.assertEqual(item.level, LEVEL_ERROR)
        self.assertIn("FOOLPROOF_PYTHON", item.advice)

    def test_item_reports_ok_for_real_interpreter(self):
        from foolproof_operation.core.audit import LEVEL_OK, AuditService

        service = AuditService(None, {})
        item = service._check_interpreter()
        self.assertEqual(item.level, LEVEL_OK)
        self.assertIn("来源", item.detail)


if __name__ == "__main__":
    unittest.main()
