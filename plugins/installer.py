"""安装器插件（行 ``installer``）：把「库名 → pip 操作」的编排收拢成一处。

依赖 ``pip`` 与 ``categories``（内核 ``inject`` 保证：缺任一即拒绝装配，fail loud）。
**只返回结构化结果，不打印、不碰界面** —— 打印格式留在 CLI、渲染留在 GUI，
因此迁移后两者的可观测输出可逐字对照（阶段 5 的「行为零变化」判据）。

编排语义逐字对齐迁移前的实现：

- 安装：逐库执行，成功/失败分别计数（对应 ``InstallerApp.install`` 与 ``cli.cmd_install``）；
- 卸载：`pip uninstall -y`，包名经 ``pip_query_name`` 处理（本地路径型 value 回退显示名）；
- 检查：先取一次 ``pip list --outdated``，再逐库 ``pip show`` 取已装版本
  （对应 ``cli.cmd_check`` 的输出判据：未安装 / 已装 X → 最新 Y（可更新）/ 已装 X（最新））；
- 升级：`install -U`（对应 ``cli.cmd_install --upgrade`` 与 GUI 的一键更新）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.context import Service

__all__ = ["InstallerService", "OperationResult", "CheckResult"]


@dataclass
class OperationResult:
    """一次安装/卸载/升级的结果汇总。"""

    action: str = ""
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: List[Tuple[str, int, str]] = field(default_factory=list)  # (目标, rc, stderr)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.succeeded > 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "results": list(self.results),
        }


@dataclass
class CheckResult:
    """单库的版本检查结果。"""

    name: str
    installed: Optional[str] = None
    latest: Optional[str] = None
    outdated_known: bool = True

    @property
    def status(self) -> str:
        if self.installed is None:
            return "missing"          # 未安装
        if self.latest and self.latest != self.installed:
            return "outdated"         # 可更新
        return "current"              # 最新（或未知）


class InstallerService(Service):
    """安装器服务（插件行 ``installer``）。

    配置：``python_exe``（目标解释器，默认取 ``pip`` 服务的设置）。
    """

    name = "installer"
    inject: Sequence[str] = ("pip", "categories")

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        self.python_exe: Optional[str] = (self.config or {}).get("python_exe")

    # ── 内部取件（缺失即 fail loud，而不是静默降级）─────

    @property
    def pip(self) -> Any:
        return self.ctx.get("pip")

    @property
    def categories(self) -> Any:
        return self.ctx.get("categories")

    def _target(self, name: str) -> str:
        return self.categories.target_of(name)

    def _query_name(self, name: str, target: Optional[str] = None) -> str:
        return self.categories.pip_query_name(name, target if target is not None else self._target(name))

    # ── 操作 ─────────────────────────────────────────

    def install(
        self, names: Sequence[str], *, targets: Optional[Sequence[str]] = None, upgrade: bool = False
    ) -> OperationResult:
        """逐库安装。``targets`` 可直接给 pip 参数（``-e <path>`` 等已拆好的形式）。"""
        result = OperationResult(action="upgrade" if upgrade else "install")
        for name in names:
            args = list(targets) if targets is not None else [self._target(name)]
            rc, err = self.pip.install(args, python_exe=self.python_exe, upgrade=upgrade)
            result.results.append((name, rc, err))
            result.total += 1
            if rc == 0:
                result.succeeded += 1
            else:
                result.failed += 1
        return result

    def uninstall(self, names: Sequence[str]) -> OperationResult:
        """逐库卸载（包名经 ``pip_query_name`` 规范化）。"""
        result = OperationResult(action="uninstall")
        for name in names:
            rc, err = self.pip.uninstall([self._query_name(name)], python_exe=self.python_exe)
            result.results.append((name, rc, err))
            result.total += 1
            if rc == 0:
                result.succeeded += 1
            else:
                result.failed += 1
        return result

    def check(self, names: Sequence[str]) -> List[CheckResult]:
        """版本检查：先取一次 outdated 表，再逐库 pip show。"""
        outdated = self.pip.outdated(self.python_exe)
        outcome: List[CheckResult] = []
        for name in names:
            query = self._query_name(name)
            version = self.pip.installed_version(query, python_exe=self.python_exe)
            if version is None:
                outcome.append(
                    CheckResult(name=name, installed=None, outdated_known=bool(outdated))
                )
                continue
            row = outdated.get(query.lower())
            outcome.append(
                CheckResult(
                    name=name,
                    installed=version,
                    latest=(row or {}).get("latest_version"),
                    outdated_known=bool(outdated),
                )
            )
        return outcome

    def __repr__(self) -> str:
        return "<InstallerService python={0}>".format(self.python_exe)
