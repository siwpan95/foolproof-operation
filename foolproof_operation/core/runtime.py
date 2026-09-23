"""运行时装配助手：读清单 → 事务化挂载 → 得到一个可直接用的 ``ctx``。

把「怎么把插件行装起来」这件事从业务代码里抽出来，供 CLI / GUI / 脚本共用：

    from .runtime import build_runtime

    with build_runtime() as rt:                 # 默认读包内的 foolproof.composition.json
        scanner = rt.ctx.get("scanner")
        environments = scanner.discover()

    with build_runtime({"fileindex": {"volumes": ["G:"]}}) as rt:   # 按行 id 覆盖配置
        ...

``build_runtime`` 的退出即 ``ctx.dispose()``：所有服务与后台线程按内核语义**逆序回收**，
因此调用方不需要记得关索引库或停线程。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Sequence

from .context import Context
from .loader import DEFAULT_ROWS, Loader, Row, load_composition, parse_rows

__all__ = ["Runtime", "build_runtime", "default_manifest_path", "merge_row_config"]

#: 包根目录（core/ 的上一级）。
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 默认清单文件名。
MANIFEST_NAME = "foolproof.composition.json"


def default_manifest_path() -> str:
    return os.path.join(PACKAGE_ROOT, MANIFEST_NAME)


def merge_row_config(rows: Sequence[Any], overrides: Optional[Dict[str, Dict[str, Any]]]) -> list:
    """把 ``{行 id: {配置键: 值}}`` 合并进清单（覆盖式，深一层）。

    覆盖值里显式为 ``None`` 的键会被**移除**（用于「不限制」这类语义，如 ``max_records=None``）。
    """
    if not overrides:
        return list(rows)
    merged = []
    for row in rows:
        if isinstance(row, Row):
            row = {
                "id": row.id,
                "plugin": row.plugin,
                "config": dict(row.config or {}),
                "isolate": list(row.isolate),
                "disabled": row.disabled,
            }
        else:
            row = dict(row)
            row["config"] = dict(row.get("config") or {})
        patch = overrides.get(row["id"])
        if patch:
            for key, value in patch.items():
                if value is None:
                    row["config"].pop(key, None)
                else:
                    row["config"][key] = value
        merged.append(row)
    return merged


class Runtime:
    """一个装配好的运行时：``ctx`` + 卸载入口（也可作上下文管理器）。"""

    def __init__(self, ctx: Context, loader: Loader, manifest: Optional[str] = None) -> None:
        self.ctx = ctx
        self.loader = loader
        self.manifest = manifest

    def get(self, name: str, default: Any = None) -> Any:
        return self.ctx.get(name, default)

    def mounted(self) -> list:
        return self.loader.mounted

    def dispose(self) -> None:
        self.ctx.dispose()

    def close(self) -> None:
        self.dispose()

    def __enter__(self) -> "Runtime":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.dispose()

    def __repr__(self) -> str:
        return "<Runtime manifest={0} rows={1}>".format(self.manifest, self.loader.mounted)


def build_runtime(
    overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    manifest: Optional[str] = None,
    rows: Optional[Sequence[Any]] = None,
    strict: bool = True,
) -> Runtime:
    """装配运行时。

    - ``rows`` 显式给出清单时不读文件（测试用）；
    - ``manifest`` 缺省读包内 ``foolproof.composition.json``，文件缺失时回退内置 ``DEFAULT_ROWS``；
    - ``strict=True``（默认）时任何一行装配失败即整体回滚并抛错；
      ``strict=False`` 时逐行容错（失败的行走 ``loader.failed`` 记录），用于「尽力而为」的 GUI 场景。
    """
    ctx = Context()
    if rows is not None:
        resolved = merge_row_config(rows, overrides)
        loader = Loader(ctx, resolved)
    else:
        path = manifest or default_manifest_path()
        if os.path.isfile(path):
            loader = Loader(ctx, merge_row_config(load_composition(path), overrides))
        else:
            loader = Loader(ctx, merge_row_config(DEFAULT_ROWS, overrides))
        manifest = path
    if strict:
        loader.mount_all()
    else:
        loader.mount_all_tolerant()
    return Runtime(ctx, loader, manifest)
