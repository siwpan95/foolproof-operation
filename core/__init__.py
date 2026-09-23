"""foolproof-operation 内核（Cordis 式可逆插件内核，**零第三方依赖**）。

对外导出：

- ``Context`` / ``Service``：服务注册表 + 可逆副作用栈 + 类式插件基类；
- ``EventBus`` / ``bus_plugin``：事件总线与其装配插件；
- ``Loader`` / ``Row`` / ``parse_rows`` / ``import_plugin`` / ``load_composition``：清单装配；
- 错误类型：``CordisError`` / ``DuplicateServiceError`` / ``MissingDependencyError`` /
  ``DuplicateScopeError`` / ``DisposedError`` / ``CompositionError``。

设计与实测依据见 ``docs/usn-indexer.md``；规划见
``G:\\docs\\md\\allin主工作区\\USN索引与Cordis内核规划_2026-09-23.md``。
"""

from __future__ import annotations

from .context import (
    Context,
    CordisError,
    DisposedError,
    DuplicateScopeError,
    DuplicateServiceError,
    MissingDependencyError,
    Service,
    resolve_plugin,
)
from .events import EventBus, bus_plugin
from .loader import (
    DEFAULT_ROWS,
    CompositionError,
    Loader,
    Row,
    import_plugin,
    load_composition,
    parse_rows,
)

__all__ = [
    "Context",
    "Service",
    "resolve_plugin",
    "EventBus",
    "bus_plugin",
    "Loader",
    "Row",
    "DEFAULT_ROWS",
    "parse_rows",
    "import_plugin",
    "load_composition",
    "CordisError",
    "DuplicateServiceError",
    "MissingDependencyError",
    "DuplicateScopeError",
    "DisposedError",
    "CompositionError",
]
