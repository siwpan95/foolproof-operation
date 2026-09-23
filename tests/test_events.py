"""事件总线测试：观察型 emit / 中间件 waterfall / 退订与随上下文回收。"""

from __future__ import annotations

import unittest

from core import Context
from core.events import EventBus, bus_plugin


class EventBusTest(unittest.TestCase):
    def test_emit_calls_listeners_and_returns_count(self):
        bus = EventBus()
        seen = []
        bus.on("evt", lambda a, b: seen.append((a, b)))
        bus.on("evt", lambda a, b: seen.append(("second", a, b)))
        self.assertEqual(bus.emit("evt", 1, 2), 2)
        self.assertEqual(seen, [(1, 2), ("second", 1, 2)])
        self.assertEqual(bus.emit("nobody-listens"), 0)

    def test_emit_ignores_return_values(self):
        bus = EventBus()
        bus.on("evt", lambda: "ignored")
        self.assertEqual(bus.emit("evt"), 1)  # 返回被调用的监听器数，而非监听器返回值

    def test_on_returns_disposer_and_off_works(self):
        bus = EventBus()
        seen = []

        def listener():
            seen.append(1)

        dispose = bus.on("evt", listener)
        self.assertEqual(bus.emit("evt"), 1)
        dispose()
        self.assertEqual(bus.emit("evt"), 0)
        self.assertFalse(bus.off("evt", listener))

    def test_once_fires_single_time(self):
        bus = EventBus()
        seen = []
        bus.once("evt", lambda: seen.append(1))
        bus.emit("evt")
        bus.emit("evt")
        self.assertEqual(seen, [1])
        self.assertEqual(bus.listeners("evt"), ())

    def test_listener_exception_is_tolerated_and_reported(self):
        errors = []
        bus = EventBus(on_error=lambda name, exc: errors.append((name, str(exc))))
        seen = []

        def boom():
            raise RuntimeError("listener 抛异常")

        bus.on("evt", boom)
        bus.on("evt", lambda: seen.append("ok"))
        self.assertEqual(bus.emit("evt"), 2)
        self.assertEqual(seen, ["ok"])  # 其余监听器照常收到
        self.assertEqual(errors, [("evt", "listener 抛异常")])

    def test_waterfall_passes_value_through_middleware(self):
        bus = EventBus()
        bus.on("chain", lambda value: value + 1)
        bus.on("chain", lambda value: None)  # 返回 None = 不改写
        bus.on("chain", lambda value: value * 10)
        self.assertEqual(bus.waterfall("chain", 1), 20)

    def test_waterfall_listener_exception_keeps_current_value(self):
        bus = EventBus()

        def boom(value):
            raise RuntimeError("middleware 抛异常")

        bus.on("chain", boom)
        bus.on("chain", lambda value: value + 5)
        self.assertEqual(bus.waterfall("chain", 1), 6)

    def test_listeners_snapshot_is_immutable(self):
        bus = EventBus()
        bus.on("evt", lambda: None)
        snapshot = bus.listeners("evt")
        bus.on("evt", lambda: None)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(len(bus.listeners("evt")), 2)
        self.assertEqual(bus.events(), ["evt"])

    def test_clear_removes_listeners(self):
        bus = EventBus()
        bus.on("a", lambda: None)
        bus.on("b", lambda: None)
        bus.clear("a")
        self.assertEqual(bus.listeners("a"), ())
        self.assertEqual(len(bus.listeners("b")), 1)
        bus.clear()
        self.assertEqual(bus.events(), [])


class BusPluginTest(unittest.TestCase):
    def test_bus_plugin_registers_service(self):
        ctx = Context()
        ctx.install(bus_plugin)
        bus = ctx.get("bus")
        self.assertIsInstance(bus, EventBus)

    def test_subscriptions_are_reclaimed_with_context(self):
        ctx = Context()
        ctx.install(bus_plugin)
        bus = ctx.get("bus")
        seen = []
        bus.on("evt", lambda: seen.append(1))
        self.assertEqual(bus.emit("evt"), 1)
        ctx.dispose()
        self.assertEqual(bus.listeners("evt"), ())
        self.assertEqual(bus.emit("evt"), 0)
        self.assertEqual(seen, [1])

    def test_subscription_disposer_registered_as_effect(self):
        """订阅也是副作用：ctx.effect(退订函数) 后，销毁即退订。"""
        ctx = Context()
        bus = EventBus()
        seen = []
        unsubscribe = bus.on("evt", lambda: seen.append(1))
        ctx.effect(unsubscribe)
        ctx.dispose()
        bus.emit("evt")
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
