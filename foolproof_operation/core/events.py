"""轻量事件总线：观察型 ``emit`` + 中间件型 ``waterfall``。

语义与本机 ``allin_engine.core.event_bus`` 对齐（观察型事件忽略返回值、监听器异常不
影响其余监听器），同样**内联重写、零代码复制**。

总线作为服务装配（见 ``bus_plugin``）：``ctx.provide("bus", bus, dispose=bus.clear)``，
订阅随上下文销毁一并回收 —— 即「监听器也是副作用」。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["EventBus", "bus_plugin"]

#: 监听器签名：emit 为 ``fn(*args)``；waterfall 为 ``fn(value, *args)``，返回非 None 即新值。
Listener = Callable[..., Any]


class EventBus:
    """同步事件总线（无第三方依赖）。

    - ``emit(name, *args)``：**观察型**广播，忽略返回值，返回被调用的监听器数；
      单个监听器抛异常被吞掉（可经 ``on_error`` 收集），不中断其余监听器；
    - ``waterfall(name, value, *args)``：**中间件链**，逐个监听器串行传递当前值，
      监听器返回非 None 即作为下一个监听器看到的输入（否决/改写语义）。
    """

    def __init__(self, *, on_error: Optional[Callable[[str, BaseException], None]] = None) -> None:
        self._listeners: Dict[str, List[Listener]] = {}
        self._on_error = on_error

    # ── 订阅 ──────────────────────────────────────────

    def on(self, name: str, fn: Listener) -> Callable[[], None]:
        """订阅事件，返回退订函数（可直接登记为 effect）。"""
        if not callable(fn):
            raise TypeError("监听器需可调用对象")
        self._listeners.setdefault(name, []).append(fn)

        def dispose() -> None:
            self.off(name, fn)

        return dispose

    def once(self, name: str, fn: Listener) -> Callable[[], None]:
        """只触发一次；触发或退订后均自动摘除。"""

        def wrapper(*args: Any) -> Any:
            dispose()
            return fn(*args)

        dispose = self.on(name, wrapper)
        return dispose

    def off(self, name: str, fn: Listener) -> bool:
        """退订；不存在返回 False。"""
        listeners = self._listeners.get(name)
        if not listeners or fn not in listeners:
            return False
        listeners.remove(fn)
        if not listeners:
            self._listeners.pop(name, None)
        return True

    def listeners(self, name: str) -> Tuple[Listener, ...]:
        """当前监听器快照（元组，调用方改动不影响总线）。"""
        return tuple(self._listeners.get(name, ()))

    def events(self) -> List[str]:
        """已订阅的事件名清单。"""
        return sorted(self._listeners)

    # ── 派发 ──────────────────────────────────────────

    def emit(self, name: str, *args: Any) -> int:
        """观察型广播：忽略返回值，返回被调用的监听器数；单个异常不影响其余。"""
        count = 0
        for fn in self.listeners(name):
            try:
                fn(*args)
            except Exception as exc:  # 容错：不中断其余监听器
                if self._on_error is not None:
                    self._on_error(name, exc)
            count += 1
        return count

    def waterfall(self, name: str, value: Any, *args: Any) -> Any:
        """中间件链：逐个监听器串行传递；监听器返回非 None 即成为新值。"""
        current = value
        for fn in self.listeners(name):
            try:
                result = fn(current, *args)
            except Exception as exc:
                if self._on_error is not None:
                    self._on_error(name, exc)
                continue
            if result is not None:
                current = result
        return current

    # ── 回收 ──────────────────────────────────────────

    def clear(self, name: Optional[str] = None) -> None:
        """清空指定事件的全部监听器（``name`` 为 None 时清空总线）。"""
        if name is None:
            self._listeners.clear()
        else:
            self._listeners.pop(name, None)

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._listeners.values())
        return "<EventBus events={0} listeners={1}>".format(len(self._listeners), total)


def bus_plugin(ctx: Any, config: Any = None) -> None:
    """内置插件：把 ``EventBus`` 注册为 ``bus`` 服务，订阅随上下文回收。"""
    bus = EventBus()
    ctx.provide("bus", bus, dispose=bus.clear)
