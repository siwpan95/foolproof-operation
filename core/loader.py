"""配置装配：插件行声明 + **事务化**装配（任一行失败 → 整体回滚）。

Cordis 的「配置即数据」在零依赖约束下的落地：清单用 JSON
（``foolproof.composition.json``，stdlib 解析；不引 PyYAML 以守住 README 的
「纯标准库」承诺，偏差记账见 ``docs/usn-indexer.md`` §4.4）。

一行 = 一个插件：

```json
{"id": "bus", "plugin": "core.events:bus_plugin"}
{"id": "fileindex", "plugin": "plugins.fileindex:FileIndexService", "config": {"volumes": ["G:", "E:"]}, "isolate": ["es"]}
{"id": "legacy-es", "plugin": "plugins.search:es_plugin", "disabled": true}
```

``plugin`` 支持 ``包.模块:属性`` 或 ``包.模块``（后者优先取模块内的 ``plugin`` 属性，
否则取模块本身）。装配语义：每行落在**以行 id 命名的 fork 作用域**内，
``isolate`` 列表中的服务在该作用域内不向上可见。
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .context import Context, CordisError, resolve_plugin

__all__ = [
    "Row",
    "CompositionError",
    "Loader",
    "DEFAULT_ROWS",
    "parse_rows",
    "import_plugin",
    "load_composition",
]

#: 内置默认清单（阶段 4/5 逐步追加：categories / pip / installer / scanner / reqtxt /
#: fileindex / search / gui / cli —— 见 G:\docs\md\allin主工作区\USN索引与Cordis内核规划_2026-09-23.md）
DEFAULT_ROWS: List[Dict[str, Any]] = [
    {"id": "bus", "plugin": "core.events:bus_plugin"},
]


class CompositionError(CordisError):
    """清单非法或装配失败（fail loud：绝不留「装了一半」的残局）。"""


@dataclass(frozen=True)
class Row:
    """一个插件行。"""

    id: str
    plugin: str
    config: Optional[Dict[str, Any]] = None
    isolate: Tuple[str, ...] = ()
    disabled: bool = False


def parse_rows(raw: Any) -> List[Row]:
    """把清单原始数据（dict 列表）校验并转成 ``Row`` 列表；非法即抛 ``CompositionError``。

    已归一化的 ``Row`` 列表原样通过（便于 ``Loader`` 混用两种输入）。
    """
    if not isinstance(raw, (list, tuple)):
        raise CompositionError(
            "清单需为列表（每项一个插件行），实际为 {0}".format(type(raw).__name__)
        )
    rows: List[Row] = []
    seen: Dict[str, int] = {}
    for index, item in enumerate(raw):
        if isinstance(item, Row):
            if item.id in seen:
                raise CompositionError(
                    "行 id 重复：'{0}'（第 {1} 行与第 {2} 行）".format(item.id, seen[item.id], index + 1)
                )
            seen[item.id] = index + 1
            rows.append(item)
            continue
        where = "清单第 {0} 行".format(index + 1)
        if not isinstance(item, dict):
            raise CompositionError("{0} 需为对象，实际为 {1}".format(where, type(item).__name__))
        row_id = item.get("id")
        plugin = item.get("plugin")
        if not isinstance(row_id, str) or not row_id:
            raise CompositionError("{0} 缺少非空 'id'".format(where))
        if not isinstance(plugin, str) or not plugin:
            raise CompositionError("行 '{0}' 缺少非空 'plugin'".format(row_id))
        if row_id in seen:
            raise CompositionError(
                "行 id 重复：'{0}'（第 {1} 行与第 {2} 行）".format(row_id, seen[row_id], index + 1)
            )
        seen[row_id] = index + 1
        config = item.get("config")
        if config is not None and not isinstance(config, dict):
            raise CompositionError("行 '{0}' 的 'config' 需为对象".format(row_id))
        isolate = item.get("isolate") or ()
        if isinstance(isolate, str) or not isinstance(isolate, (list, tuple)):
            raise CompositionError("行 '{0}' 的 'isolate' 需为字符串列表".format(row_id))
        if any(not isinstance(k, str) or not k for k in isolate):
            raise CompositionError("行 '{0}' 的 'isolate' 含空元素".format(row_id))
        disabled = item.get("disabled", False)
        if not isinstance(disabled, bool):
            raise CompositionError("行 '{0}' 的 'disabled' 需为布尔值".format(row_id))
        rows.append(
            Row(
                id=row_id,
                plugin=plugin,
                config=config,
                isolate=tuple(isolate),
                disabled=disabled,
            )
        )
    return rows


def import_plugin(target: str) -> Any:
    """按 ``包.模块:属性`` / ``包.模块`` 解析插件对象；解析失败抛 ``CompositionError``。"""
    if not isinstance(target, str) or not target:
        raise CompositionError("插件目标需为非空字符串")
    module_path, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        raise CompositionError("无法导入插件模块 '{0}': {1}".format(module_path, exc)) from exc
    if attr:
        try:
            return getattr(module, attr)
        except AttributeError as exc:
            raise CompositionError(
                "模块 '{0}' 无属性 '{1}'".format(module_path, attr)
            ) from exc
    if hasattr(module, "plugin"):
        return module.plugin
    return module


def load_composition(path: Any) -> List[Row]:
    """读取 JSON 清单文件并校验；文件缺失或 JSON 非法即抛 ``CompositionError``。

    兼容两种顶层形状：``[row, ...]`` 与 ``{"plugins": [row, ...]}``。
    """
    p = Path(path)
    if not p.is_file():
        raise CompositionError("清单文件不存在: {0}".format(p))
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CompositionError("清单文件读取失败: {0}".format(exc)) from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise CompositionError("清单 JSON 解析失败: {0}".format(exc)) from exc
    if isinstance(raw, dict) and "plugins" in raw:
        raw = raw["plugins"]
    return parse_rows(raw)


class Loader:
    """按清单装配插件行；**事务化**：任一行失败则回滚本次装配的全部行。

    用法::

        ctx = Context()
        loader = Loader(ctx, DEFAULT_ROWS)
        loader.mount_all()          # 失败 → 已装的行全部回滚后抛 CompositionError
        loader.unmount_all()        # 逐行卸载（逆序）
    """

    def __init__(
        self,
        ctx: Context,
        rows: Optional[Sequence[Any]] = None,
        *,
        importer: Any = None,
    ) -> None:
        self.ctx = ctx
        if rows is None:
            self.rows: List[Row] = []
        elif all(isinstance(r, Row) for r in rows):
            self.rows = list(rows)
        else:
            self.rows = parse_rows(list(rows))
        self._importer = importer or import_plugin
        self._mounts: List[Tuple[str, Context]] = []
        #: 容错装配时失败的行走这里（``{行 id: 原因}``）。
        self.failed: Dict[str, str] = {}

    # ── 状态 ──────────────────────────────────────────

    @property
    def mounted(self) -> List[str]:
        """已装配的行 id（按装配顺序）。"""
        return [name for name, _ in self._mounts]

    def scopes(self) -> List[Tuple[str, Context]]:
        """已装配行的 ``(id, fork)`` 清单。"""
        return list(self._mounts)

    # ── 装配 / 卸载 ───────────────────────────────────

    def mount_row(self, row: Row) -> Optional[Context]:
        """装配一行；``disabled`` 行跳过（返回 None），失败抛 ``CompositionError``。"""
        if row.disabled:
            return None
        plugin = self._importer(row.plugin)
        name, inject, _ = resolve_plugin(plugin, row.config)
        missing = [k for k in inject if not self.ctx.has(k) and k not in row.isolate]
        if missing:
            raise CompositionError(
                "行 '{0}'（{1}）缺少依赖: {2}（已注册: {3}）".format(
                    row.id, name, missing, sorted(self.ctx.keys())
                )
            )
        fork = self.ctx.fork(row.id, isolate=row.isolate)
        try:
            fork.install(plugin, row.config)
        except Exception as exc:
            fork.dispose()
            raise CompositionError("行 '{0}' 装配失败: {1}".format(row.id, exc)) from exc
        self._mounts.append((row.id, fork))
        return fork

    def mount_all(self) -> List[str]:
        """事务化装配全部行；任一行失败 → 回滚本次已装的全部行并抛 ``CompositionError``。"""
        start = len(self._mounts)
        try:
            for row in self.rows:
                self.mount_row(row)
        except Exception:
            for _, fork in reversed(self._mounts[start:]):
                fork.dispose()
            del self._mounts[start:]
            raise
        return self.mounted

    def unmount_all(self) -> None:
        """逆序卸载本次装配的全部行。"""
        for _, fork in reversed(self._mounts):
            fork.dispose()
        self._mounts.clear()

    def mount_all_tolerant(self) -> List[str]:
        """**逐行容错**装配：某行失败不连累其它行，失败原因记入 ``failed``。

        用于「尽力而为」场景（如 GUI 启动：索引服务建不起来也不该让界面起不来）。
        与 ``mount_all`` 的事务化语义相反，故不叫同一个名字 —— 需要原子性时用 ``mount_all``。
        """
        for row in self.rows:
            try:
                self.mount_row(row)
            except Exception as exc:  # 单行失败不影响其余行
                self.failed[row.id] = str(exc)
        return self.mounted
