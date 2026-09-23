"""插件层测试：检索后端回退与溯源 / 环境聚合 / 深验证 / 旧口径保留 / 清单装配。

全部无盘（用临时目录构造假环境；检索后端有假实现与真遍历实现两种）。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from core.runtime import build_runtime, default_manifest_path, merge_row_config
from plugins.scanner import EVIDENCE_NAMES, PythonEnvironment, ScannerService, parse_pyvenv_cfg
from plugins.search import (
    EsBackend,
    IndexBackend,
    SearchBackend,
    SearchHit,
    SearchResult,
    SearchService,
    WalkBackend,
    WalkRefused,
    is_whole_drive_root,
    normalize_walk_root,
)


class FakeSearch(SearchService):
    """用假后端替掉真检索，专注测 scanner 的聚合逻辑。"""

    def __init__(self, ctx=None, config=None, hits=None, order=None):
        super().__init__(ctx, {"backend_order": order or ["fake"]})
        self._hits = list(hits or [])
        self.register("fake", _FakeBackend(self._hits))
        self.order = ["fake"]


class _FakeBackend(SearchBackend):
    name = "fake"

    def __init__(self, hits):
        self.hits = hits

    def find_names(self, names, *, roots=None, limit=None):
        wanted = {n.lower() for n in names}
        return [hit for hit in self.hits if hit.name.lower() in wanted]


def _touch(path: str, data: bytes = b"MZ") -> str:
    """写一个小文件并**关闭句柄**（测试里 `open(...).write(...)` 会把 ResourceWarning 留给 GC）。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _make_venv(root: str, *, version: str = "3.13.14", home: str = "C:\\Python313") -> None:
    os.makedirs(os.path.join(root, "Scripts"), exist_ok=True)
    os.makedirs(os.path.join(root, "Lib", "site-packages"), exist_ok=True)
    with open(os.path.join(root, "pyvenv.cfg"), "w", encoding="utf-8") as fh:
        fh.write("home = {0}\n".format(home))
        fh.write("include-system-site-packages = false\n")
        fh.write("version = {0}\n".format(version))
    for exe in ("python.exe", "pythonw.exe"):
        with open(os.path.join(root, "Scripts", exe), "wb") as fh:
            fh.write(b"MZ")


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-scanner-")
        self.venv = os.path.join(self.tmpdir, "venvA")
        _make_venv(self.venv)
        self.conda = os.path.join(self.tmpdir, "condaA")
        os.makedirs(os.path.join(self.conda, "conda-meta"), exist_ok=True)
        self.embed = os.path.join(self.tmpdir, "embedA")
        os.makedirs(self.embed, exist_ok=True)
        _touch(os.path.join(self.embed, "python.exe"))
        _touch(os.path.join(self.embed, "python313._pth"), b".")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _scanner(self):
        return ScannerService(None, {"volumes": [self.tmpdir]})

    def test_group_venv_from_pyvenv_cfg(self):
        scanner = self._scanner()
        paths = [
            os.path.join(self.venv, "pyvenv.cfg"),
            os.path.join(self.venv, "Scripts", "python.exe"),
            os.path.join(self.venv, "Scripts", "pythonw.exe"),
            os.path.join(self.venv, "Lib", "site-packages"),
        ]
        envs = scanner.group(paths, backend="fake")
        self.assertEqual(len(envs), 1)
        env = envs[0]
        self.assertEqual(env.kind, "venv")
        self.assertEqual(env.version, "3.13.14")
        self.assertEqual(env.base, "C:\\Python313")
        self.assertTrue(env.python_exe.endswith(os.path.join("Scripts", "python.exe")))
        self.assertTrue(env.pythonw_exe.endswith(os.path.join("Scripts", "pythonw.exe")))
        self.assertTrue(env.site_packages.endswith("site-packages"))
        self.assertEqual(env.backend, "fake")
        self.assertGreaterEqual(len(env.evidence), 4)

    def test_group_conda_and_embed(self):
        scanner = self._scanner()
        envs = scanner.group(
            [
                os.path.join(self.conda, "conda-meta"),
                os.path.join(self.embed, "python.exe"),
            ]
        )
        kinds = sorted(env.kind for env in envs)
        self.assertEqual(kinds, ["conda", "embed"])

    def test_group_standalone_from_scripts_python(self):
        root = os.path.join(self.tmpdir, "orphan")
        os.makedirs(os.path.join(root, "Scripts"), exist_ok=True)
        _touch(os.path.join(root, "Scripts", "python.exe"))
        envs = self._scanner().group([os.path.join(root, "Scripts", "python.exe")])
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0].root, root)  # Scripts 上提一级

    def test_template_assets_are_marked_invalid_not_dropped(self):
        template = os.path.join(self.tmpdir, "lib", "python3.9", "venv", "scripts", "nt")
        os.makedirs(template, exist_ok=True)
        _touch(os.path.join(template, "python.exe"))
        envs = self._scanner().group([os.path.join(template, "python.exe")])
        self.assertEqual(len(envs), 1)
        self.assertFalse(envs[0].valid)
        self.assertIn("模板", envs[0].detail)

    def test_empty_input(self):
        self.assertEqual(self._scanner().group([]), [])

    def test_parse_pyvenv_cfg_tolerates_missing(self):
        self.assertEqual(parse_pyvenv_cfg(os.path.join(self.tmpdir, "nope.cfg")), {})


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-validate-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cheap_validation_does_not_run_interpreter(self):
        env = PythonEnvironment(
            root=self.tmpdir, python_exe=os.path.join(self.tmpdir, "python.exe"), evidence=["x"]
        )
        _touch(env.python_exe)
        with mock.patch("subprocess.run") as run:
            ScannerService(None, {}).validate(env, deep=False)
        run.assert_not_called()
        self.assertIsNone(env.valid)
        self.assertIn("未深验证", env.detail)

    def test_deep_validation_runs_interpreter(self):
        env = PythonEnvironment(
            root=self.tmpdir, python_exe=os.path.join(self.tmpdir, "python.exe"), evidence=["x"]
        )
        _touch(env.python_exe)
        proc = mock.Mock(returncode=0, stdout="Python 3.13.14", stderr="")
        with mock.patch("subprocess.run", return_value=proc) as run:
            ScannerService(None, {}).validate(env, deep=True)
        self.assertTrue(env.valid)
        self.assertEqual(env.version, "3.13.14")
        run.assert_called_once()

    def test_missing_interpreter_is_invalid(self):
        env = PythonEnvironment(root=self.tmpdir, evidence=["x"])
        ScannerService(None, {}).validate(env)
        self.assertFalse(env.valid)
        self.assertIn("未找到解释器", env.detail)

    def test_no_evidence_is_invalid(self):
        env = PythonEnvironment(root=self.tmpdir)
        ScannerService(None, {}).validate(env)
        self.assertFalse(env.valid)
        self.assertEqual(env.detail, "无证据")


class LegacyWalkTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-legacy-")
        _make_venv(os.path.join(self.tmpdir, "proj", ".venv"))
        os.makedirs(os.path.join(self.tmpdir, "deep", "a", "b", "venv"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_legacy_finds_two_levels_only(self):
        """旧口径只查顶层 + 一层 → 深层 venv 看不见（这正是它的漏点，保留作基线）。

        断言必须按**精确路径**判：初版用 `"b" in root and root.endswith("venv")` 这类子串启发式，
        临时目录随机名恰好含 `b` 时会间歇失败（`.venv` 同样 `endswith("venv")`）。
        """
        envs = ScannerService(None, {"volumes": [self.tmpdir]}).legacy_walk()
        roots = [os.path.normcase(env.root) for env in envs]
        shallow = os.path.normcase(os.path.join(self.tmpdir, "proj", ".venv"))
        deep = os.path.normcase(os.path.join(self.tmpdir, "deep", "a", "b", "venv"))
        self.assertIn(shallow, roots)
        self.assertNotIn(deep, roots)
        self.assertTrue(all(env.kind == "legacy" for env in envs))

    def test_discover_legacy_backend(self):
        scanner = ScannerService(None, {"volumes": [self.tmpdir]})
        envs = scanner.discover(backend="legacy")
        self.assertTrue(envs)
        self.assertIsNone(scanner.last_result)  # legacy 不经检索服务


class SearchBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-search-")
        _make_venv(os.path.join(self.tmpdir, "venvA"))
        os.makedirs(os.path.join(self.tmpdir, "keep"), exist_ok=True)
        _touch(os.path.join(self.tmpdir, "keep", "pyvenv.cfg"), b"version = 3.12.1\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_walk_backend_finds_evidence(self):
        backend = WalkBackend([self.tmpdir])
        hits = backend.find_names(EVIDENCE_NAMES)
        names = sorted({hit.name.lower() for hit in hits})
        self.assertIn("pyvenv.cfg", names)
        self.assertIn("python.exe", names)
        self.assertTrue(all(hit.backend == "walk" for hit in hits))

    def test_walk_backend_honors_limit_and_roots(self):
        backend = WalkBackend([self.tmpdir])
        hits = backend.find_names(["python.exe"], limit=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(backend.find_names(["python.exe"], roots=["Z:\\nope"]), [])

    def test_walk_backend_skips_configured_dirs(self):
        hidden = os.path.join(self.tmpdir, "node_modules")
        os.makedirs(hidden, exist_ok=True)
        _touch(os.path.join(hidden, "pyvenv.cfg"), b"x=1\n")
        hits = WalkBackend([self.tmpdir]).find_names(["pyvenv.cfg"])
        self.assertFalse(any("node_modules" in hit.path for hit in hits))

    def test_drive_root_is_normalized_to_drive_root(self):
        """``"G:"`` 必须归一化为 ``"G:\\"``：否则 os.walk 会静默扫「当前目录」。"""
        self.assertEqual(normalize_walk_root("G:"), "G:\\")
        self.assertEqual(normalize_walk_root(" g: "), "G:\\")
        self.assertEqual(normalize_walk_root("G:/notpython"), "G:/notpython")
        self.assertTrue(is_whole_drive_root("G:\\"))
        self.assertTrue(is_whole_drive_root("c:/"))
        self.assertFalse(is_whole_drive_root("G:\\notpython"))

    def test_walk_refuses_whole_drive_by_default(self):
        with self.assertRaises(WalkRefused) as cm:
            WalkBackend(["G:\\"]).find_names(["python.exe"])
        self.assertIn("拒绝整盘遍历", str(cm.exception))

    def test_walk_drive_root_also_refused(self):
        """写 ``G:`` 的调用同样被拒（它等价于整盘，而不是「某个目录」）。"""
        with self.assertRaises(WalkRefused):
            WalkBackend(["G:"]).find_names(["python.exe"])

    def test_walk_budget_returns_partial_with_note(self):
        """墙钟预算兜底：超时返回部分结果并如实标注（不静默、不挂死）。"""
        deep = os.path.join(self.tmpdir, "deep")
        os.makedirs(deep, exist_ok=True)
        _touch(os.path.join(deep, "pyvenv.cfg"), b"x=1\n")
        backend = WalkBackend([self.tmpdir], budget_seconds=0.0)
        hits = backend.find_names(["pyvenv.cfg"])
        self.assertTrue(backend.budget_exceeded)
        self.assertIn("墙钟预算", backend.last_error)
        self.assertEqual(hits, [])  # 预算 0：立刻返回，不返回残缺结果冒充完整

    def test_walk_without_roots_returns_empty_with_note(self):
        backend = WalkBackend([])
        self.assertEqual(backend.find_names(["python.exe"]), [])
        self.assertIn("未配置遍历根", backend.last_error)

    def test_search_service_records_walk_refusal_in_provenance(self):
        service = SearchService(None, {"backend_order": ["walk"]})
        result = service.find(["python.exe"], backend="walk", roots=["G:\\"])
        self.assertEqual(result.hits, [])
        outcome = dict(result.attempts)["walk"]
        self.assertIn("拒绝整盘遍历", outcome)

    def test_es_backend_reports_missing_binary(self):
        backend = EsBackend(os.path.join(self.tmpdir, "absent.exe"))
        ok, why = backend.available()
        self.assertFalse(ok)
        self.assertIn("不存在", why)

    def test_es_backend_handles_everything_not_running(self):
        """rc=8（Everything 未运行）→ 不得抛错，返回空并记录原因供溯源。"""
        fake = os.path.join(self.tmpdir, "es.exe")
        _touch(fake)
        backend = EsBackend(fake)
        proc = mock.Mock(returncode=8, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=proc):
            hits = backend.find_names(["python.exe"])
        self.assertEqual(hits, [])
        self.assertIn("Everything 未运行", backend.last_error)

    def test_es_backend_parses_paths(self):
        fake = os.path.join(self.tmpdir, "es.exe")
        _touch(fake)
        proc = mock.Mock(returncode=0, stdout="C:\\a\\python.exe\n\nC:\\b\\python.exe\n", stderr="")
        with mock.patch("subprocess.run", return_value=proc):
            hits = EsBackend(fake).find_names(["python.exe"])
        self.assertEqual([hit.path for hit in hits], ["C:\\a\\python.exe", "C:\\b\\python.exe"])

    def test_index_backend_unavailable_without_service(self):
        ok, why = IndexBackend(None).available()
        self.assertFalse(ok)
        self.assertIn("未装配", why)

    def test_index_backend_reports_ready_volumes(self):
        service = mock.Mock()
        service.status.return_value = {"G:": {"status": "ready"}}
        service.search.return_value = {
            "hits": {"python.exe": [{"path": "G:\\x\\python.exe", "volume": "G:", "index": 7}]},
            "missing": [],
        }
        backend = IndexBackend(service)
        ok, why = backend.available()
        self.assertTrue(ok)
        self.assertIn("G:", why)
        hits = backend.find_names(["python.exe"])
        self.assertEqual(hits[0].path, "G:\\x\\python.exe")
        self.assertEqual(hits[0].backend, "index")


class SearchServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-searchsvc-")
        _make_venv(os.path.join(self.tmpdir, "venvA"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _service(self, order, *, es_exe=None):
        config = {"backend_order": order, "walk_roots": [self.tmpdir]}
        # 让 es 后端在测试里确定不可用：否则 Everything 真在跑时它会命中临时目录，
        # 结果随机器状态而变（首版测试就踩了这个）。
        config["es_exe"] = es_exe or os.path.join(self.tmpdir, "absent-es.exe")
        return SearchService(None, config)

    def test_auto_order_falls_back_to_walk_and_records_provenance(self):
        service = self._service(["index", "es", "walk"])
        result = service.find(["pyvenv.cfg"])
        self.assertEqual(result.backend, "walk")
        names = [name for name, _ in result.attempts]
        self.assertEqual(names, ["index", "es", "walk"])
        reasons = dict(result.attempts)
        self.assertIn("未装配", reasons["index"])
        self.assertIn("不可用", reasons["es"])
        self.assertIn("命中", reasons["walk"])

    def test_explicit_backend_avoids_fallback(self):
        service = self._service(["index", "walk"])
        result = service.find(["pyvenv.cfg"], backend="index")
        self.assertEqual(result.hits, [])
        self.assertEqual(result.backend, "")
        self.assertEqual([name for name, _ in result.attempts], ["index"])

    def test_unknown_backend_is_reported(self):
        service = self._service(["walk"])
        result = service.find(["x"], backend="nope")
        self.assertIn("未注册该后端", dict(result.attempts)["nope"])

    def test_empty_result_is_falsy_but_carries_provenance(self):
        """空结果是假值（有 __len__），因此调用方必须用 `is not None` 判存在。"""
        service = self._service(["walk"])
        result = service.find(["no-such-file-xyz"])
        self.assertFalse(bool(result))
        self.assertEqual(len(result), 0)
        self.assertTrue(result.attempts)
        self.assertIn("作答后端", result.explain())

    def test_find_paths_helper(self):
        service = self._service(["walk"])
        paths = service.find_paths(["pyvenv.cfg"])
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("pyvenv.cfg"))

    def test_register_custom_backend(self):
        service = self._service(["custom"])
        service.register("custom", _FakeBackend([SearchHit(path="X:\\a\\pyvenv.cfg", name="pyvenv.cfg")]))
        result = service.find(["pyvenv.cfg"])
        self.assertEqual(result.backend, "custom")
        self.assertEqual(result.paths(), ["X:\\a\\pyvenv.cfg"])

    def test_availability_listing(self):
        service = self._service(["index", "es", "walk"])
        self.assertEqual([name for name, _, _ in service.availability()], ["index", "es", "walk"])


class CompositionTest(unittest.TestCase):
    """清单断言刻意写成**不脆**形式：必需行齐全 + 依赖顺序约束。

    （早先写死行清单，阶段 5/6 各加一行就红一次 —— 记取教训：断依赖关系，不断全表。）
    """

    REQUIRED = ["bus", "categories", "pip", "installer", "reqtxt", "fileindex", "search", "scanner"]

    def test_default_manifest_exists_and_parses(self):
        path = default_manifest_path()
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as fh:
            data = json.loads(fh.read())
        ids = [row["id"] for row in data["plugins"]]
        for required in self.REQUIRED:
            self.assertIn(required, ids)
        self.assertEqual(len(ids), len(set(ids)), "行 id 不得重复")

    def test_runtime_mounts_real_manifest_and_orders_dependencies(self):
        with build_runtime() as rt:
            mounted = rt.mounted()
            for required in self.REQUIRED:
                self.assertIn(required, mounted)
            # 依赖必须先于消费者挂载（否则 inject 校验会 fail loud）
            self.assertLess(mounted.index("pip"), mounted.index("installer"))
            self.assertLess(mounted.index("categories"), mounted.index("installer"))
            self.assertLess(mounted.index("search"), mounted.index("scanner"))
            self.assertIsNotNone(rt.get("scanner"))
            self.assertIsNotNone(rt.get("search"))
            self.assertIsNotNone(rt.get("fileindex"))
            self.assertEqual(rt.get("scanner").inject, ("search",))

    def test_runtime_overrides_row_config(self):
        with build_runtime({"scanner": {"volumes": ["G:"], "deep": True}}) as rt:
            scanner = rt.get("scanner")
            self.assertEqual(scanner.volumes, ["G:"])
            self.assertTrue(scanner.deep)

    def test_merge_row_config_removes_none_values(self):
        rows = [{"id": "a", "plugin": "p", "config": {"keep": 1, "drop": 2}}]
        merged = merge_row_config(rows, {"a": {"drop": None, "add": 3}})
        self.assertEqual(merged[0]["config"], {"keep": 1, "add": 3})

    def test_runtime_disposes_services(self):
        with build_runtime() as rt:
            index = rt.get("fileindex")
            self.assertFalse(index.ctx.disposed)
        self.assertTrue(index.ctx.disposed)


class CliIntegrationTest(unittest.TestCase):
    """CLI 接线回归：envs 子命令在真装配下跑通，且溯源如实。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="foolproof-cli-")
        _make_venv(os.path.join(self.tmpdir, "proj", ".venv"), version="3.12.2")
        self._stdout = contextlib.redirect_stdout(io.StringIO())
        self._stdout.__enter__()

    def tearDown(self):
        self._stdout.__exit__(None, None, None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_envs_with_walk_backend_finds_env_in_temp_root(self):
        import cli

        rc = cli.run_cli(["envs", "--backend", "walk", "--volumes", self.tmpdir])
        self.assertEqual(rc, 0)

    def test_envs_legacy_backend_on_temp_root(self):
        import cli

        rc = cli.run_cli(["envs", "--backend", "legacy", "--volumes", self.tmpdir])
        self.assertEqual(rc, 0)

    def test_scan_defaults_to_original_backend(self):
        """`scan` 不带 --backend 时必须默认 walk（= 原 glob 口径，行为零变化）。"""
        import cli

        args = cli.build_parser().parse_args(["scan"])
        self.assertEqual(args.backend, "walk")

    def test_envs_parser_defaults(self):
        import cli

        args = cli.build_parser().parse_args(["envs"])
        self.assertEqual(args.backend, "auto")
        self.assertFalse(args.build)
        self.assertFalse(args.deep)

    def test_scan_with_index_backend_reports_provenance(self):
        import cli

        with mock.patch("core.runtime.build_runtime") as build:
            rt = mock.MagicMock()
            rt.__enter__.return_value = rt
            scanner = mock.MagicMock()
            scanner.discover.return_value = []
            result = SearchResult(attempts=[("index", "不可用：尚无已建成的索引")])
            scanner.last_result = result
            rt.ctx.get.return_value = scanner
            build.return_value = rt
            rc = cli._scan_via_plugins(mock.Mock(volumes=None), "index")
        self.assertEqual(rc, 0)
        self.assertTrue(scanner.discover.called)


if __name__ == "__main__":
    unittest.main()
