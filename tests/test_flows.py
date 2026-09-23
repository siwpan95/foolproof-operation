"""阶段 B 流程测试：flows.py 的日志文本、音效序列与计数口径（无 tkinter、无真 pip）。

断言里的字符串**逐字取自迁移前的 gui.py**（``self.log_write(...)`` 的实参），
因此这些用例就是「GUI 流程行为零变化」的回归锁。
"""

from __future__ import annotations

import unittest

from foolproof_operation.flows import (
    Sink,
    report_failure,
    run_check,
    run_install,
    run_requirements,
    run_uninstall,
    run_upgrade,
)


class Recorder:
    """记录 log / sound / fail_log 调用（log_newline=""：调用方传什么就记什么）。"""

    def __init__(self):
        self.logs = []
        self.sounds = []
        self.fail_logs = []

    def sink(self) -> Sink:
        return Sink(
            log=self.logs.append,
            sound=self.sounds.append,
            fail_log=self.fail_logs.append,
            log_newline="",
        )


class FakeInstaller:
    """脚本化安装器：按名字返回 (rc, err)，并记录调用。"""

    def __init__(self, script=None, check_rows=None):
        self.script = dict(script or {})
        self.calls = []
        self._check_rows = check_rows

    def _result(self, name, action):
        rc, err = self.script.get(name, (0, ""))
        return type("R", (), {"results": [(name, rc, err)]})()

    def install(self, names, targets=None, upgrade=False):
        name = list(names)[0]
        self.calls.append(("install", name, list(targets or []), bool(upgrade)))
        return self._result(name, "install")

    def uninstall(self, names):
        name = list(names)[0]
        self.calls.append(("uninstall", name))
        return self._result(name, "uninstall")

    def check_pairs(self, pairs):
        self.calls.append(("check_pairs", list(pairs)))
        if self._check_rows is not None:
            return list(self._check_rows)
        rows = []
        for name, _target in pairs:
            rc, err = self.script.get(name, (0, ""))
            rows.append(_row(name, rc, err))
        return rows


def _row(name, rc, err):
    from foolproof_operation.plugins.installer import CheckResult

    if rc == 0:
        return CheckResult(name=name, installed="1.0", latest=None)
    if rc == 1:
        return CheckResult(name=name, installed="1.0", latest="2.0")
    if rc == 2:
        return CheckResult(name=name, installed=None)
    return CheckResult(name=name, error=err or "boom")


PKGS = [("requests", "requests"), ("allin-engine", "-e G:/repos/an-engine")]


class InstallFlowTest(unittest.TestCase):
    def test_success_and_failure_lines(self):
        rec = Recorder()
        installer = FakeInstaller(script={"allin-engine": (1, "boom tail")})
        counters = run_install(installer, PKGS, rec.sink())
        self.assertEqual(
            rec.logs,
            [
                "🚀 开始安装依赖库...\n",
                "📦 安装 requests",
                "✅ requests 安装成功\n",
                "📦 安装 allin-engine",
                "❌ allin-engine 安装失败：\n    boom tail\n",
                "🎉 安装完成！",
            ],
        )
        self.assertEqual(rec.sounds, ["ok", "fail", "done"])
        self.assertEqual(rec.fail_logs, ["❌ allin-engine 安装失败：\n    boom tail"])
        self.assertEqual(counters, {"total": 2, "ok": 1, "failed": 1})

    def test_uses_split_spec_and_upgrade_flag(self):
        rec = Recorder()
        installer = FakeInstaller()
        run_install(installer, PKGS, rec.sink())
        self.assertEqual(installer.calls[0], ("install", "requests", ["requests"], True))
        self.assertEqual(installer.calls[1], ("install", "allin-engine", ["-e", "G:/repos/an-engine"], True))

    def test_failure_without_tail_has_no_colon_newline(self):
        rec = Recorder()
        installer = FakeInstaller(script={"requests": (1, "")})
        run_install(installer, [PKGS[0]], rec.sink())
        # 与迁移前一致：无 stderr 尾部时不带「：\n」，只有一行「❌ requests 安装失败」+ 换行
        self.assertIn("❌ requests 安装失败\n", rec.logs)
        self.assertNotIn("❌ requests 安装失败：\n", rec.logs)


class UpgradeFlowTest(unittest.TestCase):
    def test_pip_is_upgraded_last_and_uses_plain_target(self):
        rec = Recorder()
        installer = FakeInstaller()
        packages = [("pip", "pip"), ("zzz", "zzz"), ("aaa", "aaa")]
        run_upgrade(installer, packages, rec.sink())
        names = [call[1] for call in installer.calls]
        self.assertEqual(names, ["aaa", "zzz", "pip"])
        self.assertEqual(installer.calls[-1][2], ["pip"])
        self.assertTrue(all(call[3] for call in installer.calls))
        self.assertEqual(rec.logs[0], "⬆️ 开始更新依赖库...\n")
        self.assertEqual(rec.logs[-1], "🎉 更新完成！")
        self.assertEqual(rec.sounds.count("ok"), 3)
        self.assertEqual(rec.sounds[-1], "done")


class UninstallFlowTest(unittest.TestCase):
    def test_lines_and_calls(self):
        rec = Recorder()
        installer = FakeInstaller(script={"requests": (1, "nope")})
        counters = run_uninstall(installer, PKGS, rec.sink())
        self.assertEqual(installer.calls[0], ("uninstall", "requests"))
        self.assertEqual(
            rec.logs,
            [
                "🗑️ 开始卸载依赖库...\n",
                "🗑️ 卸载 requests",
                "❌ requests 卸载失败：\n    nope\n",
                "🗑️ 卸载 allin-engine",
                "✅ allin-engine 卸载成功\n",
                "🎉 卸载完成！",
            ],
        )
        self.assertEqual(counters, {"total": 2, "ok": 1, "failed": 1})


class CheckFlowTest(unittest.TestCase):
    def test_all_four_branches(self):
        from foolproof_operation.plugins.installer import CheckResult

        rec = Recorder()
        rows = [
            CheckResult(name="requests", installed="1.0", latest="2.0"),
            CheckResult(name="flask", installed="3.0", latest=None),
            CheckResult(name="ghost", installed=None),
            CheckResult(name="broken", error="timeout"),
        ]
        installer = FakeInstaller(check_rows=rows)
        counters = run_check(installer, [("requests", "requests")], rec.sink())
        self.assertEqual(
            rec.logs,
            [
                "🔍 正在检查版本...\n",
                "📦 requests: 1.0  →  2.0 (可更新)",
                "📦 flask: 3.0 (已是最新)",
                "⚠️  ghost: 未安装",
                "❌ broken 检查失败：timeout",
                "\n✅ 版本检查完成！",
            ],
        )
        self.assertEqual(rec.fail_logs, ["❌ broken 检查失败：timeout"])
        self.assertEqual(rec.sounds, ["done"])
        self.assertEqual(counters, {"total": 4, "missing": 1, "outdated": 1, "current": 1, "error": 1})

    def test_check_pairs_receives_gui_pairs_verbatim(self):
        from foolproof_operation.plugins.installer import CheckResult

        rec = Recorder()
        installer = FakeInstaller(check_rows=[CheckResult(name="x", installed="1")])
        pairs = [("allin-engine", "-e G:/repos/an-engine")]
        run_check(installer, pairs, rec.sink())
        self.assertEqual(installer.calls[0], ("check_pairs", pairs))


class RequirementsFlowTest(unittest.TestCase):
    def test_header_separator_and_summary(self):
        rec = Recorder()
        installer = FakeInstaller(script={"b": (1, "bad")})
        packages = [("a", "a>=1.0"), ("b", "b==2.0")]
        counters = run_requirements(installer, packages, "G:/x/requirements.txt", rec.sink())
        self.assertEqual(rec.logs[0], "\n🚀 从 requirements.txt 安装 2 个依赖包…\n")
        self.assertEqual(rec.logs[1], "   源文件: G:/x/requirements.txt\n")
        self.assertEqual(rec.logs[2], "📦 安装 a (a>=1.0)")
        self.assertEqual(rec.logs[3], "✅ a 安装成功\n")
        self.assertIn("❌ b 安装失败：\n    bad\n", rec.logs)
        self.assertIn("\n" + "=" * 50, rec.logs)
        self.assertEqual(rec.logs[-1], "📊 安装完成！成功 1 个，失败 1 个")
        self.assertEqual(counters, {"total": 2, "ok": 1, "failed": 1})
        self.assertEqual(installer.calls[0], ("install", "a", ["a>=1.0"], False))


class ReportFailureTest(unittest.TestCase):
    def test_message_with_tail_and_hint(self):
        rec = Recorder()
        msg = report_failure(rec.sink(), "x", "安装", "x", "line1\nline2\nNo matching distribution found")
        self.assertEqual(rec.sounds, ["fail"])
        self.assertIn("❌ x 安装失败：", msg)
        self.assertIn("    No matching distribution found", msg)
        self.assertEqual(rec.fail_logs, [msg])
        self.assertTrue(any("💡" in line and "发行包" in line for line in rec.logs))

    def test_empty_stderr_has_no_tail_and_no_hint(self):
        rec = Recorder()
        msg = report_failure(rec.sink(), "x", "卸载", "x", "")
        self.assertEqual(msg, "❌ x 卸载失败")
        self.assertFalse(any("💡" in line for line in rec.logs))

    def test_sink_newline_default_for_other_callers(self):
        """非 GUI 调用方可用默认换行策略（log_newline="\\n"）。"""
        lines = []
        sink = Sink(log=lines.append)
        sink.write("abc")
        self.assertEqual(lines, ["abc\n"])


if __name__ == "__main__":
    unittest.main()
