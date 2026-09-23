"""清单装配测试：校验 fail loud / 事务化整体回滚 / 行级 isolate / 跨行依赖可见性。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from core import Context, Loader, Row
from core.events import EventBus
from core.loader import (
    DEFAULT_ROWS,
    CompositionError,
    import_plugin,
    load_composition,
    parse_rows,
)


# ── 测试用插件（模块级，供 importer 映射引用）────────────────────────────


def provider_plugin(ctx, config):
    ctx.provide("value", 7)


provider_plugin.name = "provider_plugin"


class ConsumerPlugin:
    """声明 inject 的对象式插件：装配期即校验依赖是否到位。"""

    name = "consumer_plugin"
    inject = ("value",)

    def apply(self, ctx, config):
        ctx.provide("consumed", ctx.get("value"))


def boom_plugin(ctx, config):
    ctx.provide("partial", 1)
    raise RuntimeError("装配中途失败")


boom_plugin.name = "boom_plugin"


def es_reader_plugin(ctx, config):
    """读取隔离服务：用于验证行级 isolate 语义。"""
    ctx.provide("seen_es", ctx.get("es", default=None))


es_reader_plugin.name = "es_reader_plugin"


_TEST_PLUGINS = {
    "provider_plugin": provider_plugin,
    "consumer_plugin": ConsumerPlugin,
    "boom_plugin": boom_plugin,
    "es_reader_plugin": es_reader_plugin,
}


def _importer(target):
    return _TEST_PLUGINS[target]


class ParseRowsTest(unittest.TestCase):
    def test_valid_rows(self):
        rows = parse_rows(
            [
                {"id": "a", "plugin": "pkg.mod:attr"},
                {"id": "b", "plugin": "pkg.mod", "config": {"k": 1}, "isolate": ["x"], "disabled": True},
            ]
        )
        self.assertEqual([r.id for r in rows], ["a", "b"])
        self.assertEqual(rows[1].config, {"k": 1})
        self.assertEqual(rows[1].isolate, ("x",))
        self.assertTrue(rows[1].disabled)

    def test_row_objects_pass_through(self):
        rows = parse_rows([Row(id="a", plugin="pkg.mod:attr")])
        self.assertEqual(rows[0].id, "a")

    def test_invalid_inputs_fail_loud(self):
        cases = [
            {"plugins": "not-a-list"},
            [42],
            [{"plugin": "pkg"}],
            [{"id": "", "plugin": "pkg"}],
            [{"id": "a"}],
            [{"id": "a", "plugin": "pkg"}, {"id": "a", "plugin": "other"}],
            [{"id": "a", "plugin": "pkg", "config": [1]}],
            [{"id": "a", "plugin": "pkg", "isolate": "x"}],
            [{"id": "a", "plugin": "pkg", "isolate": [""]}],
            [{"id": "a", "plugin": "pkg", "disabled": "yes"}],
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(CompositionError):
                    parse_rows(case)


class ImportPluginTest(unittest.TestCase):
    def test_resolves_attr_and_module(self):
        self.assertTrue(callable(import_plugin("core.events:bus_plugin")))
        module = import_plugin("core.events")
        self.assertIs(module, sys.modules["core.events"])
        self.assertTrue(callable(module.bus_plugin))

    def test_unknown_module_and_attr_fail_loud(self):
        with self.assertRaises(CompositionError):
            import_plugin("no.such.module:plugin")
        with self.assertRaises(CompositionError):
            import_plugin("core.events:no_such_attr")
        with self.assertRaises(CompositionError):
            import_plugin("")


class LoadCompositionTest(unittest.TestCase):
    def _write(self, payload):
        tmp = Path(tempfile.mkdtemp()) / "foolproof.composition.json"
        tmp.write_text(payload, encoding="utf-8")
        return tmp

    def test_reads_list_and_plugins_wrapper(self):
        p1 = self._write(json.dumps([{"id": "bus", "plugin": "core.events:bus_plugin"}]))
        self.assertEqual(load_composition(p1)[0].id, "bus")
        p2 = self._write(json.dumps({"plugins": [{"id": "bus", "plugin": "core.events:bus_plugin"}]}))
        self.assertEqual(load_composition(p2)[0].id, "bus")

    def test_missing_file_and_bad_json_fail_loud(self):
        with self.assertRaises(CompositionError):
            load_composition(Path(tempfile.mkdtemp()) / "absent.json")
        with self.assertRaises(CompositionError):
            load_composition(self._write("{ not json"))


class LoaderTest(unittest.TestCase):
    def test_default_rows_mount_bus(self):
        ctx = Context()
        loader = Loader(ctx, DEFAULT_ROWS)
        self.assertEqual(loader.mount_all(), ["bus"])
        self.assertIsInstance(ctx.get("bus"), EventBus)
        self.assertEqual([name for name, _ in ctx.scopes()], ["bus"])

    def test_disabled_row_is_skipped(self):
        ctx = Context()
        loader = Loader(
            ctx,
            [
                {"id": "provider", "plugin": "provider_plugin"},
                {"id": "off", "plugin": "boom_plugin", "disabled": True},
            ],
            importer=_importer,
        )
        self.assertEqual(loader.mount_all(), ["provider"])
        self.assertEqual(ctx.get("value"), 7)

    def test_cross_row_injection_sees_earlier_row_service(self):
        """行级 fork 不切断跨行依赖：注册冒泡到 realm 根，后续行可注入。"""
        ctx = Context()
        loader = Loader(
            ctx,
            [
                {"id": "provider", "plugin": "provider_plugin"},
                {"id": "consumer", "plugin": "consumer_plugin"},
            ],
            importer=_importer,
        )
        self.assertEqual(loader.mount_all(), ["provider", "consumer"])
        self.assertEqual(ctx.get("consumed"), 7)

    def test_missing_inject_dependency_fails_loud_with_row_id(self):
        ctx = Context()
        loader = Loader(ctx, [{"id": "consumer", "plugin": "consumer_plugin"}], importer=_importer)
        with self.assertRaises(CompositionError) as cm:
            loader.mount_all()
        self.assertIn("consumer", str(cm.exception))
        self.assertIn("value", str(cm.exception))
        self.assertEqual(ctx.scopes(), [])

    def test_transactional_rollback_removes_earlier_rows(self):
        """核心不变量：任一行失败 → 本次装配全部回滚，不留「装了一半」的残局。"""
        ctx = Context()
        loader = Loader(
            ctx,
            [
                {"id": "provider", "plugin": "provider_plugin"},
                {"id": "boom", "plugin": "boom_plugin"},
            ],
            importer=_importer,
        )
        with self.assertRaises(CompositionError) as cm:
            loader.mount_all()
        self.assertIn("boom", str(cm.exception))
        self.assertEqual(loader.mounted, [])
        self.assertEqual(ctx.scopes(), [])
        self.assertFalse(ctx.has("value"))  # 第一行发布的服务已回滚
        self.assertFalse(ctx.has("partial"))  # 失败行的半成品已回滚

    def test_row_level_isolate_hides_named_service(self):
        ctx = Context()
        ctx.provide("es", "outer-es")
        loader = Loader(
            ctx,
            [{"id": "reader", "plugin": "es_reader_plugin", "isolate": ["es"]}],
            importer=_importer,
        )
        loader.mount_all()
        self.assertIsNone(ctx.get("seen_es"))  # 行内看不到外层的 es
        self.assertEqual(ctx.get("es"), "outer-es")  # 外层未被破坏

    def test_unmount_all_rolls_back_rows_and_keeps_others(self):
        ctx = Context()
        ctx.provide("keep", "outside")
        loader = Loader(
            ctx,
            [{"id": "provider", "plugin": "provider_plugin"}],
            importer=_importer,
        )
        loader.mount_all()
        loader.unmount_all()
        self.assertEqual(loader.mounted, [])
        self.assertFalse(ctx.has("value"))
        self.assertEqual(ctx.get("keep"), "outside")
        self.assertEqual(ctx.scopes(), [])

    def test_unknown_plugin_module_reports_row(self):
        ctx = Context()
        loader = Loader(ctx, [{"id": "ghost", "plugin": "no.such.module:thing"}])
        with self.assertRaises(CompositionError) as cm:
            loader.mount_all()
        self.assertIn("no.such.module", str(cm.exception))
        self.assertEqual(ctx.scopes(), [])


if __name__ == "__main__":
    unittest.main()
