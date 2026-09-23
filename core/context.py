"""最小 Cordis 式内核：服务注册表（realm）+ 可逆副作用（effect）栈。

设计语义对齐 ``G:\\repos\\an-engine\\allin_engine\\core\\context.py``（siwpan，MIT）与
Cordis 官方语义，按仓库法「各工具保持独立运行（复制优于共享）、互不 import」
**内联重写、零代码复制** —— 借鉴记账见 ``docs/usn-indexer.md`` §1。

三条不变量（本项目遵守）：

1. **一切副作用显式化**：注册服务 / 挂监听 / 开线程，都经 ``provide``/``effect`` 登记，
   上下文销毁时**逆序、串行、容错**回收（单个 disposer 抛异常不中断其余回滚）。
2. **依赖声明化**：插件用 ``inject`` 声明所需服务，缺失即拒绝装配（fail loud），
   不允许「先装配、运行时才发现服务不存在」。
3. **服务可见性与回收责任分离**（Cordis 语义，本项目关键设计）：

   - **注册冒泡**：``provide`` 默认把服务登记到**所属 realm 的根**上，因此
     A 行发布的 ``bus``，B 行能注入 —— 每行各自 fork 的生命周期隔离不会切断跨行依赖；
   - **回收留在注册者**：disposer 与摘除责任登记在**注册者本层**，
     因此卸载 B 行 fork 时，B 行发布到 root 的服务也被摘掉（不留孤儿服务）；
   - **isolate 按服务名隔离**：``fork(isolate=("es",))`` 使该子树对 ``es`` 既看不到外层，
     也不把 ``es`` 外泄给兄弟/父层（多环境、多后端互不串味）。

   等价说法：**服务是全局的，effect 是 fiber 的** —— 这正是 Cordis「一插件一 Fiber、
   服务可被整棵树解析」的实现方式，也是「行级 fork 隔离」与「跨行依赖」能同时成立的根因。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

__all__ = [
    "Context",
    "Service",
    "CordisError",
    "DuplicateServiceError",
    "MissingDependencyError",
    "DuplicateScopeError",
    "DisposedError",
    "resolve_plugin",
]

_MISSING = object()


class _Entry:
    """effect 栈条目：可逆副作用 + 其服务归属。

    - ``key``：服务名（``None`` 表示匿名 effect）；
    - ``dispose``：disposer（可为 None）；
    - ``owner``：服务的保管上下文（realm 根）；
    - ``service``：注册的服务对象（用于身份校验，避免误删他人替换后的同名服务）；
    - ``holder``：``_entries`` 所属的上下文（= 注册者；回收时从这里摘除）。
    """

    __slots__ = ("key", "dispose", "owner", "service", "holder")

    def __init__(
        self,
        key: Optional[str],
        dispose: Optional[Callable[[], Any]],
        owner: "Context",
        service: Any,
        holder: "Context",
    ) -> None:
        self.key = key
        self.dispose = dispose
        self.owner = owner
        self.service = service
        self.holder = holder


# ── 错误类型（fail loud，不用静默覆盖掩盖装配问题）────────────────────────


class CordisError(Exception):
    """内核装配与生命周期错误基类。"""


class DuplicateServiceError(CordisError):
    """同一 realm 重复注册同一服务 key。"""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            "服务 '{0}' 已在同一 realm 注册；如确需替换请传 replace=True".format(key)
        )


class MissingDependencyError(CordisError):
    """查找或装配缺失的依赖。"""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class DuplicateScopeError(CordisError):
    """同名 fork 作用域尚未销毁。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            "作用域 '{0}' 尚未销毁；同名 fork 会混叠（先 dispose 再 fork）".format(name)
        )


class DisposedError(CordisError):
    """对已销毁的上下文做注册操作。"""

    def __init__(self, detail: str = "上下文已销毁，不能再注册服务/副作用") -> None:
        super().__init__(detail)


# ── 插件形态归一 ────────────────────────────────────────────────────────


def _service_runner(
    cls: type, config: Any, instance: Optional["Service"] = None
) -> Tuple[str, List[str], Callable[[Any], Any]]:
    """Service 子类/实例 → ``(name, inject, runner(ctx))``。"""
    name = instance.name if instance is not None else (cls.name or cls.__name__.lower())

    def runner(ctx: "Context", _cfg: Any = config) -> Any:
        svc = instance if instance is not None else cls(ctx, _cfg)
        if not svc.name:
            svc.name = name
        ctx.provide(svc.name, svc, dispose=getattr(svc, "stop", None))
        start = getattr(svc, "start", None)
        if callable(start):
            start()
        apply_fn = getattr(svc, "apply", None)
        if callable(apply_fn):
            result = apply_fn()
            return result if callable(result) else None
        return None

    inject = list(getattr(instance if instance is not None else cls, "inject", None) or [])
    return name, inject, runner


def resolve_plugin(plugin: Any, config: Any = None) -> Tuple[str, List[str], Callable[[Any], Any]]:
    """归一化三种插件形态 → ``(name, inject, runner)``。

    - **函数式**：``def plugin(ctx, config) -> Optional[disposer]``（可挂 ``plugin.name`` / ``plugin.inject``）；
    - **对象式**：带 ``apply(ctx, config)`` 的对象（``name`` / ``inject`` 属性可选）；
    - **类式**：``Service`` 子类（构造 ``(ctx, config)``，自带 ``name`` / ``inject`` / ``start`` / ``stop`` / ``apply``）。
    """
    if isinstance(plugin, Service):
        return _service_runner(type(plugin), config, instance=plugin)
    if isinstance(plugin, type) and issubclass(plugin, Service):
        return _service_runner(plugin, config)
    if isinstance(plugin, type):
        # 普通类（非 Service 子类）：要求类上定义了 apply，按无参构造实例后调用
        # instance.apply(ctx, config) —— 与 Cordis「插件可以是类」对齐。
        apply_attr = getattr(plugin, "apply", None)
        if not callable(apply_attr):
            raise CordisError(
                "不支持以普通类作为插件（{0}）：请继承 Service，或提供无参可构造 + apply 的类".format(
                    plugin.__name__
                )
            )
        name = getattr(plugin, "name", None) or plugin.__name__
        inject = list(getattr(plugin, "inject", None) or [])

        def runner(ctx: "Context", _cfg: Any = config) -> Any:
            try:
                instance = plugin()
            except Exception as exc:
                raise CordisError(
                    "插件类 '{0}' 需可无参构造（配置经 apply 传入）: {1}".format(plugin.__name__, exc)
                ) from exc
            return apply_attr(instance, ctx, {} if _cfg is None else _cfg)

        return name, inject, runner
    if callable(plugin):
        name = getattr(plugin, "name", None) or getattr(plugin, "__name__", None) or repr(plugin)
        inject = list(getattr(plugin, "inject", None) or [])

        def runner(ctx: "Context", _cfg: Any = config) -> Any:
            return plugin(ctx, {} if _cfg is None else _cfg)

        return name, inject, runner
    apply_fn = getattr(plugin, "apply", None)
    if callable(apply_fn):
        name = getattr(plugin, "name", None) or type(plugin).__name__
        inject = list(getattr(plugin, "inject", None) or [])

        def runner(ctx: "Context", _cfg: Any = config) -> Any:
            return apply_fn(ctx, {} if _cfg is None else _cfg)

        return name, inject, runner
    raise CordisError(
        "无法识别的插件形态: {0!r}（需函数 / 带 apply 的对象 / Service 子类）".format(plugin)
    )


# ── Service 基类 ───────────────────────────────────────────────────────


class Service:
    """能力单位基类（类式插件）。

    子类声明 ``name``（服务名，默认取类名小写）与 ``inject``（依赖服务名列表），
    可选实现 ``start`` / ``stop``（生命周期钩子，``stop`` 会被登记为该服务的 disposer）
    与 ``apply``（装配主体；返回值若可调用则作为额外 disposer）。
    """

    name: str = ""
    inject: Sequence[str] = ()

    def __init__(self, ctx: "Context", config: Any = None) -> None:
        self.ctx = ctx
        self.config: Dict[str, Any] = {} if config is None else config
        if not self.name:
            self.name = type(self).__name__.lower()

    def start(self) -> None:
        """服务激活钩子（``provide`` 之后调用）。"""

    def stop(self) -> None:
        """服务回收钩子（作为该服务的 disposer，随注册者上下文逆序回收）。"""

    def apply(self) -> Any:
        """装配主体；返回可调用对象时作为额外 disposer。"""
        return None


# ── Context ────────────────────────────────────────────────────────────


class Context:
    """服务注册表（realm）+ 可逆副作用栈。

    用法::

        ctx = Context()
        ctx.provide("bus", bus, dispose=bus.clear)      # 注册即绑定回收
        ctx.effect(lambda: print("undo"))               # 匿名副作用
        ctx.install(my_plugin, {"k": 1})                # 按 name/inject/apply 协议装配
        fork = ctx.mount(row_plugin, name="env-314")    # 行级/实例级作用域
        ctx.dispose()                                   # 逆序回滚全部注册
    """

    def __init__(self, parent: Optional["Context"] = None) -> None:
        self._parent = parent
        self._services: Dict[str, Any] = {}
        self._entries: List[_Entry] = []
        self._children: List["Context"] = []
        self._barriers: Set[str] = set()
        self._published: Set[str] = set()
        self._outer_done: Set[str] = set()
        self._fork_seq: Dict[str, int] = {}
        self._name: Optional[str] = None
        self._disposed = False

    # ── 注册 ──────────────────────────────────────────

    def provide(
        self,
        key: str,
        service: Any,
        *,
        dispose: Optional[Callable[[], Any]] = None,
        replace: bool = False,
    ) -> Any:
        """注册服务（可附带 disposer）；返回 service 便于链式赋值。

        默认登记到**所属 realm 的根**（同名可见于整棵树），但回收责任留在本层 ——
        详见模块 docstring 第 3 条不变量。``isolate`` 命中的 key 只在本层可见。
        """
        self._ensure_live()
        if not isinstance(key, str) or not key:
            raise ValueError("服务 key 需为非空字符串")
        owner = self._registry_ctx(key)
        if key in owner._services:
            if not replace:
                raise DuplicateServiceError(key)
            owner._drop_key(key)
        owner._services[key] = service
        self._entries.append(_Entry(key, dispose, owner, service, self))
        self._published.add(key)
        return service

    def effect(self, fn: Callable[[], Any]) -> None:
        """登记匿名可逆副作用：上下文销毁时逆序执行。"""
        self._ensure_live()
        if not callable(fn):
            raise TypeError("effect 需可调用对象（disposer）")
        self._entries.append(_Entry(None, fn, self, None, self))

    # ── 查找 ──────────────────────────────────────────

    def has(self, key: str) -> bool:
        """本上下文或父链上是否存在该服务（``isolate`` 屏障会阻断向上查找）。"""
        if key in self._services:
            return True
        if key in self._barriers:
            return False
        return self._parent.has(key) if self._parent is not None else False

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """按 key 查找：先本层、再父链；缺失且无 default 时抛 ``MissingDependencyError``。"""
        if key in self._services:
            return self._services[key]
        if key in self._barriers:
            if default is _MISSING:
                raise MissingDependencyError(
                    "服务 '{0}' 在本隔离作用域内不可见（isolate 屏障）".format(key)
                )
            return default
        if self._parent is not None:
            value = self._parent.get(key, _MISSING)
            if value is not _MISSING:
                return value
        if default is _MISSING:
            raise MissingDependencyError(
                "未找到服务 '{0}'（可见服务: {1}）".format(key, self.visible_keys())
            )
        return default

    def keys(self) -> List[str]:
        """**本层**保管的 key（不含父链）。"""
        return list(self._services.keys())

    def published(self) -> List[str]:
        """**本层**发布过的 key（含冒泡到 realm 根注册的）。"""
        return sorted(self._published)

    def visible_keys(self) -> List[str]:
        """本上下文可见的全部服务名（沿父链向上，尊重 ``isolate`` 屏障）。"""
        keys = set(self._services)
        if self._parent is not None:
            for name in self._parent.visible_keys():
                if name in self._barriers or name in keys:
                    continue
                keys.add(name)
        return sorted(keys)

    def parent(self) -> Optional["Context"]:
        return self._parent

    @property
    def name(self) -> Optional[str]:
        """fork 作用域名（顶层或匿名子作用域为 None）。"""
        return self._name

    @property
    def disposed(self) -> bool:
        return self._disposed

    # ── 插件装配 ──────────────────────────────────────

    def install(self, plugin: Any, config: Any = None) -> "Context":
        """按插件协议装配到**本上下文**：``name`` + ``inject`` + ``apply``。

        ``inject`` 中的依赖缺失时**拒绝装配**（fail loud）；``apply`` 返回值若可调用，
        登记为该插件的统一 disposer。
        """
        self._ensure_live()
        name, inject, runner = resolve_plugin(plugin, config)
        missing = [k for k in inject if not self.has(k)]
        if missing:
            raise MissingDependencyError(
                "插件 '{0}' 缺少依赖: {1}（可见服务: {2}）".format(
                    name, missing, self.visible_keys()
                )
            )
        result = runner(self)
        if callable(result):
            self.effect(result)
        return self

    def mount(
        self,
        plugin: Any,
        config: Any = None,
        *,
        name: Optional[str] = None,
        isolate: Sequence[str] = (),
    ) -> "Context":
        """把插件装配到**命名 fork 作用域**（行级/实例级多实例）；返回该 fork。

        同一插件可多实例并存，各自副作用随 fork 独立回收；装配失败自动回滚该 fork
        （不留半装配状态）。多实例的**私有服务名**须列入 ``isolate``，否则第二次挂载
        会因同一 realm 内重名而 fail loud（与 Cordis「一插件多实例需 isolate realm」同构）。
        """
        self._ensure_live()
        if name is None:
            name, _, _ = resolve_plugin(plugin, config)
        fork = self.fork(name, isolate=isolate)
        try:
            fork.install(plugin, config)
        except Exception:
            fork.dispose()
            raise
        return fork

    def unmount(self, name: str) -> bool:
        """销毁指定命名作用域（含其全部副作用与其发布的服务）；不存在返回 False。"""
        for child in list(self._children):
            if child._name == name:
                child.dispose()
                return True
        return False

    # ── 卸载 / 销毁 ───────────────────────────────────

    def unload(self, key: str) -> bool:
        """只回滚指定 key 的注册（含其 disposer）；未注册返回 False。"""
        owner = self._registry_ctx(key)
        if key not in owner._services:
            return False
        return owner._drop_key(key)

    def dispose(self) -> None:
        """销毁本上下文：递归销毁子作用域，再**逆序、串行、容错**回滚本层全部副作用。

        幂等；单个 disposer 抛异常不中断其余回滚；本层发布的（可能登记在 realm 根的）
        服务同步摘除；销毁后自动从父上下文摘除（反复 fork/dispose 不累积）。
        """
        if self._disposed:
            return
        for child in list(self._children):
            child.dispose()
        for entry in reversed(self._entries):
            if entry.dispose is not None:
                try:
                    entry.dispose()
                except Exception:
                    pass  # 容错：单个 disposer 失败不影响其余回滚
            if entry.key is not None and entry.owner._services.get(entry.key) is entry.service:
                del entry.owner._services[entry.key]
        self._entries.clear()
        self._services.clear()
        self._published.clear()
        self._disposed = True
        if self._parent is not None and self in self._parent._children:
            self._parent._children.remove(self)

    # ── 作用域：fork / isolate / scopes ────────────────

    def child(self) -> "Context":
        """匿名子作用域（``fork(None)`` 的等价形式）。"""
        return self.fork(None)

    def fork(self, name: Optional[str] = None, *, isolate: Sequence[str] = ()) -> "Context":
        """创建（命名）子作用域。

        - 查找沿父链向上（``get`` / ``has``）；本层 ``provide`` 默认冒泡到 realm 根；
        - 同一 realm 内重复注册同名服务抛 ``DuplicateServiceError``（fail loud），
          需要私有副本/遮蔽（shadow）请用 ``isolate`` 声明该服务名；
        - ``isolate`` 列出的服务名在本子树内**既不向上可见、也不外泄**（Cordis isolate 语义）；
        - 销毁逆序回滚本层副作用并自动从父上下文摘除；
        - 同名作用域未销毁时重复 fork 抛 ``DuplicateScopeError``，防作用域混叠。
        """
        self._ensure_live()
        if name is not None and any(c._name == name for c in self._children):
            raise DuplicateScopeError(name)
        child = Context(parent=self)
        child._name = name
        child._barriers = set(isolate)
        self._children.append(child)
        return child

    def isolate(self, *keys: str) -> "Context":
        """创建隔离作用域：``keys`` 中的服务在本子树内不与外层互通（多实例互不串味）。"""
        return self.fork(None, isolate=keys)

    def fork_install(
        self,
        plugin: Any,
        config: Any = None,
        *,
        name: Optional[str] = None,
        isolate: Sequence[str] = (),
    ) -> "Context":
        """reusable/fork 双作用域装配：``outer`` 只执行一次，``apply`` 每次落独立 fork。

        插件若提供 ``outer(ctx)`` 钩子（可重用外作用域：共享状态、一次性注册），
        按插件名在本上下文去重执行，其副作用挂本上下文；``apply`` 每次都在新 fork 内装配
        （实例私有服务名列入 ``isolate``，见 ``mount``）。
        """
        self._ensure_live()
        pname = getattr(plugin, "name", None) or type(plugin).__name__
        outer = getattr(plugin, "outer", None)
        if callable(outer) and pname not in self._outer_done:
            outer(self)
            self._outer_done.add(pname)
        seq = self._fork_seq.get(pname, 0) + 1
        self._fork_seq[pname] = seq
        scope = name if name is not None else "{0}#{1}".format(pname, seq)
        return self.mount(plugin, config, name=scope, isolate=isolate)

    def scopes(self) -> List[Tuple[Optional[str], "Context"]]:
        """当前存活的子作用域清单 ``[(name, child), ...]``。"""
        return [(c._name, c) for c in self._children]

    # ── 内部 ──────────────────────────────────────────

    def _registry_ctx(self, key: str) -> "Context":
        """该 key 的注册归属上下文：默认冒泡到 realm 根；遇 ``isolate`` 命中即停在隔离层。"""
        node = self
        while node._parent is not None and key not in node._barriers:
            node = node._parent
        return node

    def _find_entry(self, key: str) -> Optional[_Entry]:
        """查找该 key 的 effect 条目：先本层（后进先出），再递归子作用域。"""
        for entry in reversed(self._entries):
            if entry.key == key:
                return entry
        for child in self._children:
            found = child._find_entry(key)
            if found is not None:
                return found
        return None

    def _drop_key(self, key: str) -> bool:
        """回滚并摘除某服务（含其 disposer）；调用方须保证 ``self`` 是保管者。"""
        entry = self._find_entry(key)
        if entry is None:
            if key in self._services:
                del self._services[key]
                return True
            return False
        if entry.dispose is not None:
            try:
                entry.dispose()
            except Exception:
                pass
        entry.holder._entries = [e for e in entry.holder._entries if e is not entry]
        if entry.owner._services.get(key) is entry.service:
            del entry.owner._services[key]
        entry.holder._published.discard(key)
        return True

    def _ensure_live(self) -> None:
        if self._disposed:
            raise DisposedError()
        if self._parent is not None:
            self._parent._ensure_live()

    def __repr__(self) -> str:
        return "<Context name={0!r} keys={1} scopes={2}{3}>".format(
            self._name,
            sorted(self._services),
            [n for n, _ in self.scopes()],
            " disposed" if self._disposed else "",
        )
