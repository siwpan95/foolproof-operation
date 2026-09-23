"""阶段 5 插件测试：分类清单 / pip / reqtxt / 安装器（无盘、无网络、不真装包）。

行为同一性另有硬证据：`%TEMP%\\golden_before.txt`（HEAD 快照）与 `golden_after.txt`（迁移后）
在 52 项断言上逐字一致 —— 由 `git archive HEAD` + 探针脚本产出，见阶段 5 汇报。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from foolproof_operation.core.context import Context
from foolproof_operation.core.runtime import build_runtime
from foolproof_operation.plugins.categories import (
    AUTO_DISCOVERY_KEY,
    CategoriesError,
    CategoriesService,
    all_packages,
    audit,
    default_categories_path,
    find_category,
    is_local_spec,
    load_categories,
    pip_query_name,
    resolve_packages,
    split_spec,
    target_of,
)
from foolproof_operation.plugins.installer import CheckResult, InstallerService, OperationResult
from foolproof_operation.plugins.pip import PipService, failure_hint, stderr_tail
from foolproof_operation.plugins.reqtxt import ReqTxtService, parse_requirements_text, parse_requirements_txt


class CategoriesTest(unittest.TestCase):
    def setUp(self):
        self.categories = load_categories()

    def test_real_manifest_stats(self):
        service = CategoriesService(None, {"path": default_categories_path()})
        stats = service.stats()
        self.assertEqual(stats["categories"], 24)
        self.assertEqual(stats["entries"], 761)
        self.assertEqual(stats["auto_discovery"], 3)
        self.assertEqual(stats["total_entries"], 764)
        self.assertNotIn(AUTO_DISCOVERY_KEY, service.curated_categories())

    def test_audit_accepts_real_manifest(self):
        self.assertEqual(audit(self.categories), [])

    def test_audit_rejects_bad_shapes(self):
        cases = [
            [],
            {"cat": []},
            {"cat": {}},
            {"cat": {"": "pkg"}},
            {"cat": {"pkg": ""}},
            {"cat": {"pkg": None}},
            {"": {"pkg": "pkg"}},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertTrue(audit(case))

    def test_load_rejects_bad_manifest_and_missing_file(self):
        tmp = tempfile.mkdtemp(prefix="foolproof-cat-")
        try:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"cat": []}')
            with self.assertRaises(CategoriesError):
                load_categories(bad)
            with self.assertRaises(CategoriesError):
                load_categories(os.path.join(tmp, "absent.json"))
            broken = os.path.join(tmp, "broken.json")
            with open(broken, "w", encoding="utf-8") as fh:
                fh.write("{ not json")
            with self.assertRaises(CategoriesError):
                load_categories(broken)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_find_category_exact_fuzzy_and_errors(self):
        keys = list(self.categories)
        self.assertEqual(find_category(self.categories, keys[0]), keys[0])
        self.assertIn("加密", find_category(self.categories, "加密"))
        with self.assertRaises(SystemExit) as cm:
            find_category(self.categories, "不存在的分类")
        self.assertIn("未知分类", str(cm.exception))
        with self.assertRaises(SystemExit) as cm2:
            find_category(self.categories, "包")  # 多个分类命中 → 歧义
        self.assertIn("分类歧义", str(cm2.exception))

    def test_target_and_query_name_semantics(self):
        self.assertEqual(target_of(self.categories, "requests"), "requests")
        self.assertEqual(target_of(self.categories, "allin-engine"), "-e G:/repos/an-engine")
        self.assertEqual(target_of(self.categories, "not-in-list"), "not-in-list")
        self.assertEqual(pip_query_name("fastapi", "fastapi[all]"), "fastapi")
        self.assertEqual(pip_query_name("allin-engine", "-e G:/repos/an-engine"), "allin-engine")
        self.assertTrue(is_local_spec("-e G:/repos/an-engine"))
        self.assertTrue(is_local_spec("G:/repos/x"))
        self.assertFalse(is_local_spec("requests"))

    def test_split_spec_variants(self):
        self.assertEqual(split_spec("requests"), ["requests"])
        self.assertEqual(split_spec("fastapi[all]"), ["fastapi[all]"])
        self.assertEqual(split_spec("-e G:/repos/an-engine"), ["-e", "G:/repos/an-engine"])
        self.assertEqual(split_spec("-e G:\\repos\\an-engine"), ["-e", "G:\\repos\\an-engine"])
        self.assertEqual(split_spec('"C:/my pkg"'), ["C:/my pkg"])
        self.assertEqual(split_spec('"unpaired'), ['"unpaired'])  # 引号不配对 → 整体传参
        self.assertEqual(split_spec(""), [])

    def test_all_packages_dedup_and_resolve(self):
        pkgs = all_packages(self.categories)
        self.assertEqual(len(pkgs), len(set(pkgs)))
        self.assertEqual(resolve_packages(self.categories, ["requests"], None, None, False), ["requests"])
        self.assertEqual(resolve_packages(self.categories, ["requests", "requests"], None, None, False),
                         ["requests"])
        self.assertTrue(resolve_packages(self.categories, [], "加密", None, False))
        with self.assertRaises(SystemExit):
            resolve_packages(self.categories, [], None, None, False)

    def test_resolve_packages_reads_file(self):
        tmp = tempfile.mkdtemp(prefix="foolproof-catfile-")
        try:
            path = os.path.join(tmp, "req.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# c\naiohttp\n\n  requests  \n")
            self.assertEqual(
                resolve_packages(self.categories, [], None, path, False), ["aiohttp", "requests"]
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PipTest(unittest.TestCase):
    def test_stderr_tail(self):
        self.assertEqual(stderr_tail(None), "")
        self.assertEqual(stderr_tail("   "), "")
        self.assertEqual(stderr_tail("a\nb\nc\nd", lines=2), "    c\n    d")

    def test_failure_hint_mapping(self):
        self.assertIn("发行包", failure_hint("No matching distribution found for x"))
        self.assertIn("版本号不满足", failure_hint("Could not find a version that satisfies"))
        self.assertIn("构建工具", failure_hint("metadata-generation-failed"))
        self.assertIn("网络超时", failure_hint("Read timed out."))
        self.assertIn("证书", failure_hint("SSL: CERTIFICATE_VERIFY_FAILED"))
        self.assertEqual(failure_hint("其它错误"), "")
        self.assertEqual(failure_hint(None), "")

    def test_run_swallows_timeout_and_errors(self):
        service = PipService(None, {"timeout": 5})
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 5)):
            rc, err = service.run(["python", "-m", "pip", "list"])
        self.assertEqual(rc, -1)
        self.assertIn("命令超时（>5 秒）", err)
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            rc2, err2 = service.run(["x"])
        self.assertEqual(rc2, -1)
        self.assertEqual(err2, "boom")

    def test_run_full_returns_stdout(self):
        proc = mock.Mock(returncode=0, stdout="out", stderr="err")
        with mock.patch("subprocess.run", return_value=proc):
            rc, out, err = PipService(None, {}).run_full(["x"])
        self.assertEqual((rc, out, err), (0, "out", "err"))

    def test_call_live_uses_inherited_stdio(self):
        with mock.patch("subprocess.call", return_value=0) as call:
            rc = PipService(None, {"python_exe": "PY"}).call_live(["install", "requests"])
        self.assertEqual(rc, 0)
        call.assert_called_once_with(["PY", "-m", "pip", "install", "requests"])

    def test_install_and_uninstall_commands(self):
        service = PipService(None, {"python_exe": "PY"})
        with mock.patch.object(service, "pip", return_value=(0, "")) as pip_call:
            service.install(["requests"], upgrade=True)
            pip_call.assert_called_once_with(["install", "-U", "requests"], python_exe=None, timeout=None)
            service.uninstall(["requests"])
            self.assertEqual(pip_call.call_args[0][0], ["uninstall", "-y", "requests"])

    def test_repair_methods(self):
        service = PipService(None, {"python_exe": "PY"})
        with mock.patch.object(service, "run", return_value=(0, "")) as run:
            service.repair("upgrade")
            self.assertEqual(run.call_args[0][0], ["PY", "-m", "pip", "install", "--upgrade", "pip"])
            service.repair("ensurepip")
            self.assertEqual(run.call_args[0][0], ["PY", "-m", "ensurepip", "--upgrade"])
        with self.assertRaises(ValueError):
            service.repair("nope")

    def test_health_states(self):
        service = PipService(None, {"python_exe": "PY"})
        missing = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=missing):
            state = service.health()
        self.assertEqual(state["state"], "missing")
        self.assertEqual(state["main"], "pip 未安装")

        ok_version = mock.Mock(returncode=0, stdout="pip 24.0 from C:\\x (python 3.13)", stderr="")
        nothing_outdated = mock.Mock(returncode=0, stdout="[]", stderr="")
        with mock.patch("subprocess.run", side_effect=[ok_version, nothing_outdated]):
            state = service.health()
        self.assertEqual((state["state"], state["version"]), ("ok", "24.0"))

        outdated = mock.Mock(returncode=0, stdout='[{"name": "pip", "version": "24.0", "latest_version": "25.1"}]', stderr="")
        with mock.patch("subprocess.run", side_effect=[ok_version, outdated]):
            state = service.health()
        self.assertEqual(state["state"], "outdated")
        self.assertIn("25.1", state["detail"])

    def test_outdated_records_failure_reason(self):
        service = PipService(None, {})
        with mock.patch("subprocess.check_output", side_effect=OSError("nope")):
            self.assertEqual(service.outdated(), {})
        self.assertIsNotNone(service.last_outdated_error)
        with mock.patch("subprocess.check_output", return_value='[{"name": "Pip"}]'):
            self.assertEqual(list(service.outdated()), ["pip"])
        self.assertIsNone(service.last_outdated_error)


class ReqTxtTest(unittest.TestCase):
    def test_parse_text_rules(self):
        text = (
            "# comment\n"
            "requests>=2.0\n"
            "\n"
            "-r other.txt\n"
            "--index-url https://x\n"
            "pip install bad\n"
            "python -m pip install alsobad\n"
            "fastapi[all]\n"
            "pkg  # trailing comment\n"
            "url-pkg @ https://x/y.whl#sha256=abc\n"
        )
        packages = parse_requirements_text(text)
        names = [name for name, _ in packages]
        self.assertEqual(
            names, ["requests", "fastapi[all]", "pkg", "url-pkg"]
        )
        # #sha256 不是行内注释（# 前无空白）→ 规格保留完整 URL
        self.assertIn("#sha256=abc", packages[-1][1])
        self.assertEqual(packages[0][1], "requests>=2.0")

    def test_parse_file_encodings_and_missing(self):
        tmp = tempfile.mkdtemp(prefix="foolproof-reqtxt-")
        try:
            utf8 = os.path.join(tmp, "utf8.txt")
            with open(utf8, "w", encoding="utf-8-sig") as fh:
                fh.write("requests\n")
            self.assertEqual([n for n, _ in parse_requirements_txt(utf8)], ["requests"])
            gbk = os.path.join(tmp, "gbk.txt")
            with open(gbk, "w", encoding="gbk") as fh:
                fh.write("flask  # 中文注释\n")
            self.assertEqual([n for n, _ in parse_requirements_txt(gbk)], ["flask"])
            self.assertEqual(parse_requirements_txt(os.path.join(tmp, "absent.txt")), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_install_from_file_with_fake_pip(self):
        tmp = tempfile.mkdtemp(prefix="foolproof-reqinstall-")
        try:
            path = os.path.join(tmp, "req.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("a\nb\n")
            calls = []

            class FakePip:
                def install(self, targets, python_exe=None, upgrade=False):
                    calls.append(list(targets))
                    return (0 if targets == ["a"] else 1, "" if targets == ["a"] else "boom")

            events = []
            service = ReqTxtService(None, {})
            result = service.install_from_file(
                path, pip=FakePip(), on_event=lambda kind, payload: events.append(kind)
            )
            self.assertEqual(calls, [["a"], ["b"]])
            self.assertEqual(result["installed"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["total"], 2)
            self.assertEqual(events, ["start", "item", "item", "done"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_install_from_file_without_pip_service_reports_failure(self):
        tmp = tempfile.mkdtemp(prefix="foolproof-reqno-")
        try:
            path = os.path.join(tmp, "req.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("a\n")
            result = ReqTxtService(None, {"x": 1}).install_from_file(path, pip=None)
            self.assertEqual(result["failed"], 1)
            self.assertIn("pip 服务未装配", result["results"][0][3])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class _FakePip:
    """记录调用并返回脚本化结果的假 pip 服务。"""

    name = "pip"

    def __init__(self, *, results=None, outdated=None, versions=None):
        self.calls = []
        self.results = list(results or [])
        self._outdated = dict(outdated or {})
        self._versions = dict(versions or {})
        self.last_outdated_error = None

    def install(self, targets, python_exe=None, upgrade=False, timeout=None):
        self.calls.append(("install", list(targets), upgrade))
        return self.results.pop(0) if self.results else (0, "")

    def uninstall(self, names, python_exe=None, timeout=None):
        self.calls.append(("uninstall", list(names)))
        return self.results.pop(0) if self.results else (0, "")

    def outdated(self, python_exe=None, timeout=120):
        return dict(self._outdated)

    def installed_version(self, pkg, python_exe=None):
        return self._versions.get(pkg)


class _FakeCategories:
    name = "categories"

    def target_of(self, name):
        return "-e G:/repos/an-engine" if name == "allin-engine" else name

    def pip_query_name(self, name, pkg):
        return name if pkg.startswith("-e ") else pkg.split("[")[0]


class InstallerTest(unittest.TestCase):
    def _service(self, pip, categories=None):
        ctx = Context()
        ctx.provide("pip", pip)
        ctx.provide("categories", categories or _FakeCategories())
        ctx.install(InstallerService, {"python_exe": "PY"})
        return ctx.get("installer")

    def test_install_uses_targets_and_upgrade_flag(self):
        pip = _FakePip(results=[(0, ""), (1, "boom")])
        service = self._service(pip)
        result = service.install(["requests", "flask"], upgrade=True)
        self.assertEqual(pip.calls, [("install", ["requests"], True), ("install", ["flask"], True)])
        self.assertEqual((result.total, result.succeeded, result.failed), (2, 1, 1))
        self.assertFalse(result.ok)

    def test_install_local_package_uses_mapped_target(self):
        pip = _FakePip()
        service = self._service(pip)
        service.install(["allin-engine"])
        self.assertEqual(pip.calls[0][1], ["-e G:/repos/an-engine"])

    def test_uninstall_uses_query_name(self):
        pip = _FakePip()
        service = self._service(pip)
        service.uninstall(["allin-engine", "fastapi"])
        self.assertEqual(pip.calls[0][1], ["allin-engine"])
        self.assertEqual(pip.calls[1][1], ["fastapi"])

    def test_check_statuses(self):
        pip = _FakePip(
            outdated={"requests": {"latest_version": "3.0"}},
            versions={"requests": "2.32.0", "flask": "3.0.0"},
        )
        service = self._service(pip)
        rows = service.check(["requests", "flask", "missing-pkg"])
        by_name = {row.name: row for row in rows}
        self.assertEqual(by_name["requests"].status, "outdated")
        self.assertEqual(by_name["flask"].status, "current")
        self.assertEqual(by_name["missing-pkg"].status, "missing")
        self.assertEqual(by_name["requests"].latest, "3.0")

    def test_check_marks_outdated_unknown_when_query_failed(self):
        pip = _FakePip(versions={"flask": "3.0.0"})
        pip.outdated = lambda *a, **k: {}
        pip.last_outdated_error = "boom"
        service = self._service(pip)
        rows = service.check(["flask"])
        self.assertFalse(rows[0].outdated_known)
        self.assertEqual(rows[0].status, "current")

    def test_result_dataclasses(self):
        result = OperationResult(action="install")
        self.assertFalse(result.ok)
        result.succeeded = 1
        self.assertTrue(result.ok)
        self.assertEqual(CheckResult("x", None).status, "missing")
        self.assertEqual(CheckResult("x", "1", "2").status, "outdated")
        self.assertEqual(CheckResult("x", "1", None).status, "current")

    def test_missing_dependency_fails_loud(self):
        from foolproof_operation.core.loader import CompositionError, Loader, Row

        ctx = Context()
        loader = Loader(ctx, [Row(id="installer", plugin="foolproof_operation.plugins.installer:InstallerService")])
        with self.assertRaises(CompositionError) as cm:
            loader.mount_all()
        self.assertIn("pip", str(cm.exception))


class CompositionRowsTest(unittest.TestCase):
    def test_manifest_has_required_rows_in_dependency_order(self):
        required = ["categories", "pip", "installer", "reqtxt"]
        with build_runtime() as rt:
            mounted = rt.mounted()
            for row in required:
                self.assertIn(row, mounted)
            self.assertLess(mounted.index("pip"), mounted.index("installer"))
            self.assertLess(mounted.index("categories"), mounted.index("installer"))
            self.assertEqual(rt.get("categories").stats()["entries"], 761)
            self.assertIsNotNone(rt.get("installer"))
            self.assertIsNotNone(rt.get("reqtxt"))

    def test_pip_row_defaults_to_current_interpreter(self):
        with build_runtime() as rt:
            self.assertTrue(rt.get("pip").python_exe)


if __name__ == "__main__":
    unittest.main()
