"""内核语义测试：逆序回滚 / 重复注册 fail loud / fork 隔离与不累积 / 依赖声明 / 三种插件形态。"""

from __future__ import annotations

import unittest

from core import (
    Context,
    CordisError,
    DisposedError,
    DuplicateScopeError,
    DuplicateServiceError,
    MissingDependencyError,
    Service,
)


class _Recorder:
    """记录 disposer 调用顺序的共用夹具。"""

    def __init__(self) -> None:
        self.calls = []

    def disposer(self, tag):
        def fn():
            self.calls.append(tag)

        return fn


# ── 三种插件形态（模块级，便于复用）────────────────────────────────────


def function_plugin(ctx, config):
    """函数式插件：装配一个服务，并返回统一 disposer。"""
    ctx.provide("fn_service", {"config": config})
    return lambda: ctx.unload("fn_service")


def function_plugin_with_meta(ctx, config):
    ctx.provide("meta_service", config)


function_plugin_with_meta.name = "meta_plugin"
function_plugin_with_meta.inject = ("dep",)


class ObjectPlugin:
    """对象式插件：带 apply 的对象。"""

    name = "object_plugin"
    inject = ()

    def __init__(self) -> None:
        self.configs = []

    def apply(self, ctx, config):
        self.configs.append(config)
        ctx.provide("object_service", config)


class CounterService(Service):
    """类式插件：Service 子类，含 start/stop 钩子与 apply 装配主体。"""

    name = "counter"
    inject = ()

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self.lifecycle = []

    def start(self):
        self.lifecycle.append("start")

    def stop(self):
        self.lifecycle.append("stop")

    def apply(self):
        self.ctx.provide("counter_value", self.config.get("value", 0))


class NeedyService(Service):
    name = "needy"
    inject = ("absent_service",)

    def apply(self):
        pass


class ReusablePlugin:
    """fork/reusable 双作用域插件：outer 只执行一次，apply 每次落独立 fork。"""

    name = "reusable"

    def __init__(self) -> None:
        self.outer_calls = 0
        self.apply_calls = 0

    def outer(self, ctx):
        self.outer_calls += 1
        ctx.provide("shared_state", {"instances": []})

    def apply(self, ctx, config):
        self.apply_calls += 1
        ctx.get("shared_state")["instances"].append(config["id"])
        ctx.provide("instance_id", config["id"])


class ContextBasicsTest(unittest.TestCase):
    def test_provide_get_has_and_visible_keys(self):
        ctx = Context()
        ctx.provide("svc", 1)
        self.assertTrue(ctx.has("svc"))
        self.assertEqual(ctx.get("svc"), 1)
        self.assertEqual(ctx.visible_keys(), ["svc"])
        self.assertEqual(ctx.keys(), ["svc"])
        self.assertEqual(ctx.get("absent", default="fallback"), "fallback")
        with self.assertRaises(MissingDependencyError):
            ctx.get("absent")

    def test_provide_rejects_empty_key(self):
        ctx = Context()
        for bad in ("", None, 7):
            with self.assertRaises(ValueError):
                ctx.provide(bad, 1)

    def test_duplicate_provide_fails_loud_then_replace_works(self):
        ctx = Context()
        ctx.provide("svc", 1)
        with self.assertRaises(DuplicateServiceError):
            ctx.provide("svc", 2)
        self.assertEqual(ctx.get("svc"), 1)
        ctx.provide("svc", 2, replace=True)
        self.assertEqual(ctx.get("svc"), 2)

    def test_effect_requires_callable(self):
        ctx = Context()
        with self.assertRaises(TypeError):
            ctx.effect("not-callable")


class EffectRollbackTest(unittest.TestCase):
    def test_effects_run_in_reverse_order(self):
        rec = _Recorder()
        ctx = Context()
        ctx.effect(rec.disposer("first"))
        ctx.effect(rec.disposer("second"))
        ctx.effect(rec.disposer("third"))
        ctx.dispose()
        self.assertEqual(rec.calls, ["third", "second", "first"])

    def test_dispose_is_idempotent(self):
        rec = _Recorder()
        ctx = Context()
        ctx.effect(rec.disposer("once"))
        ctx.dispose()
        ctx.dispose()
        self.assertEqual(rec.calls, ["once"])

    def test_dispose_is_exception_tolerant(self):
        rec = _Recorder()

        def boom():
            raise RuntimeError("disposer 抛异常")

        ctx = Context()
        ctx.effect(rec.disposer("kept-1"))
        ctx.effect(boom)
        ctx.effect(rec.disposer("kept-2"))
        ctx.dispose()  # 不抛出
        self.assertEqual(rec.calls, ["kept-2", "kept-1"])

    def test_service_disposer_runs_on_dispose(self):
        ctx = Context()
        svc = CounterService(ctx, {"value": 5})
        ctx.install(svc)
        self.assertEqual(svc.lifecycle, ["start"])
        self.assertEqual(ctx.get("counter_value"), 5)
        ctx.dispose()
        self.assertEqual(svc.lifecycle, ["start", "stop"])
        self.assertFalse(ctx.has("counter"))

    def test_unload_rolls_back_single_key_only(self):
        rec = _Recorder()
        ctx = Context()
        ctx.provide("a", 1, dispose=rec.disposer("a"))
        ctx.provide("b", 2, dispose=rec.disposer("b"))
        self.assertTrue(ctx.unload("a"))
        self.assertFalse(ctx.unload("a"))  # 幂等：已摘除
        self.assertEqual(rec.calls, ["a"])
        self.assertFalse(ctx.has("a"))
        self.assertEqual(ctx.get("b"), 2)
        ctx.dispose()
        self.assertEqual(rec.calls, ["a", "b"])

    def test_disposed_context_rejects_registration(self):
        ctx = Context()
        ctx.dispose()
        with self.assertRaises(DisposedError):
            ctx.provide("svc", 1)
        with self.assertRaises(DisposedError):
            ctx.effect(lambda: None)
        with self.assertRaises(DisposedError):
            ctx.fork("child")


class ScopeTest(unittest.TestCase):
    def test_fork_lookup_ascends_to_parent(self):
        ctx = Context()
        ctx.provide("parent_svc", "P")
        fork = ctx.fork("f")
        self.assertEqual(fork.get("parent_svc"), "P")
        self.assertTrue(fork.has("parent_svc"))

    def test_fork_registration_bubbles_to_realm_root(self):
        """注册冒泡：行 fork 里发布的服务对兄弟/父层可见（Cordis 服务全局语义）。"""
        ctx = Context()
        fork_a = ctx.fork("row-a")
        fork_a.provide("shared", 42)
        fork_b = ctx.fork("row-b")
        self.assertEqual(ctx.get("shared"), 42)
        self.assertEqual(fork_b.get("shared"), 42)
        self.assertEqual(fork_a.get("shared"), 42)

    def test_unmounting_fork_removes_its_published_services(self):
        """回收留注册者：卸载 A 行后，A 行发布到 realm 根的服务同步摘除（不留孤儿）。"""
        ctx = Context()
        fork_a = ctx.fork("row-a")
        fork_a.provide("shared", 42)
        fork_b = ctx.fork("row-b")
        fork_a.dispose()
        self.assertFalse(ctx.has("shared"))
        with self.assertRaises(MissingDependencyError):
            fork_b.get("shared")
        ctx.dispose()
        self.assertEqual(ctx.scopes(), [])

    def test_duplicate_service_in_same_realm_fails_loud(self):
        ctx = Context()
        ctx.provide("svc", "root")
        fork = ctx.fork("f")
        with self.assertRaises(DuplicateServiceError):
            fork.provide("svc", "fork")

    def test_isolate_hides_outer_service_and_keeps_inner_private(self):
        ctx = Context()
        ctx.provide("es", "outer")
        ctx.provide("bus", "shared-bus")
        iso = ctx.isolate("es")
        self.assertFalse(iso.has("es"))
        with self.assertRaises(MissingDependencyError):
            iso.get("es")
        self.assertEqual(iso.get("es", default=None), None)
        self.assertEqual(iso.get("bus"), "shared-bus")  # 未隔离的服务照常可见
        iso.provide("es", "inner")  # 隔离层内可持有私有副本
        self.assertEqual(iso.get("es"), "inner")
        self.assertEqual(ctx.get("es"), "outer")  # 不外泄
        iso.dispose()
        self.assertEqual(ctx.get("es"), "outer")

    def test_duplicate_scope_name_rejected_until_disposed(self):
        ctx = Context()
        ctx.fork("f")
        with self.assertRaises(DuplicateScopeError):
            ctx.fork("f")
        ctx.unmount("f")
        ctx.fork("f")  # 销毁后同名可再建
        self.assertEqual([n for n, _ in ctx.scopes()], ["f"])

    def test_fork_dispose_does_not_accumulate(self):
        ctx = Context()
        for i in range(100):
            fork = ctx.fork("scope-{0}".format(i))
            fork.provide("tmp", i)
            fork.dispose()
        self.assertEqual(ctx.scopes(), [])
        self.assertFalse(ctx.has("tmp"))

    def test_unmount_reports_missing_scope(self):
        ctx = Context()
        self.assertFalse(ctx.unmount("never-mounted"))

    def test_dispose_cascades_to_children(self):
        ctx = Context()
        child = ctx.fork("child")
        grandchild = child.fork("grandchild")
        ctx.dispose()
        self.assertTrue(child.disposed)
        self.assertTrue(grandchild.disposed)
        self.assertEqual(ctx.scopes(), [])

    def test_mount_rolls_back_its_fork_on_failure(self):
        ctx = Context()

        def boom(ctx_, config):
            ctx_.provide("partial", 1)
            raise RuntimeError("装配中途失败")

        with self.assertRaises(RuntimeError):
            ctx.mount(boom, name="boom-row")
        self.assertEqual(ctx.scopes(), [])
        self.assertFalse(ctx.has("partial"))


class PluginFormTest(unittest.TestCase):
    def test_function_plugin_with_disposer(self):
        ctx = Context()
        ctx.install(function_plugin, {"k": "v"})
        self.assertEqual(ctx.get("fn_service"), {"config": {"k": "v"}})
        ctx.dispose()
        self.assertFalse(ctx.has("fn_service"))

    def test_object_plugin_receives_config(self):
        ctx = Context()
        plugin = ObjectPlugin()
        ctx.install(plugin, {"n": 1})
        self.assertEqual(plugin.configs, [{"n": 1}])
        self.assertEqual(ctx.get("object_service"), {"n": 1})

    def test_service_class_plugin(self):
        ctx = Context()
        ctx.install(CounterService, {"value": 9})
        self.assertIsInstance(ctx.get("counter"), CounterService)
        self.assertEqual(ctx.get("counter_value"), 9)

    def test_inject_missing_dependency_fails_loud(self):
        ctx = Context()
        with self.assertRaises(MissingDependencyError) as cm:
            ctx.install(NeedyService)
        self.assertIn("absent_service", str(cm.exception))

    def test_inject_satisfied_after_provide(self):
        ctx = Context()
        ctx.provide("dep", object())
        ctx.install(function_plugin_with_meta, {"x": 1})
        self.assertEqual(ctx.get("meta_service"), {"x": 1})

    def test_unrecognized_plugin_form(self):
        ctx = Context()
        with self.assertRaises(CordisError) as cm:
            ctx.install(42)
        self.assertIn("无法识别的插件形态", str(cm.exception))


class ForkInstallTest(unittest.TestCase):
    def test_outer_runs_once_apply_runs_per_instance(self):
        """多实例：实例私有服务用 isolate 声明（与 Cordis「多实例需 isolate realm」同构）。"""
        ctx = Context()
        plugin = ReusablePlugin()
        first = ctx.fork_install(plugin, {"id": "env-a"}, isolate=("instance_id",))
        second = ctx.fork_install(plugin, {"id": "env-b"}, isolate=("instance_id",))
        self.assertEqual(plugin.outer_calls, 1)
        self.assertEqual(plugin.apply_calls, 2)
        self.assertEqual(first.get("instance_id"), "env-a")
        self.assertEqual(second.get("instance_id"), "env-b")
        self.assertFalse(ctx.has("instance_id"))  # 实例私有：不外泄到 realm 根
        self.assertEqual(ctx.get("shared_state")["instances"], ["env-a", "env-b"])
        # 实例作用域互不干扰：销毁一个实例不影响另一个
        first.dispose()
        self.assertEqual(second.get("instance_id"), "env-b")
        ctx.dispose()
        self.assertEqual(ctx.scopes(), [])

    def test_multi_instance_without_isolate_collides(self):
        """未声明 isolate 的同名服务在同一 realm 冲突 —— fail loud，而非静默覆盖。"""
        ctx = Context()
        plugin = ReusablePlugin()
        ctx.fork_install(plugin, {"id": "env-a"})
        with self.assertRaises(DuplicateServiceError):
            ctx.fork_install(plugin, {"id": "env-b"})


if __name__ == "__main__":
    unittest.main()
