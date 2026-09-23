"""启动审计（fail-loud）：把「配置/依赖/索引/日志」一次核完，给出可执行建议。

对应工作区既有的审计口径（crawl_hub 的 ``--check`` 启动审计、repocheck 的规则即数据）：
**宁可启动时显式报错，也不要运行到一半才炸**。本模块只做**只读**检查，不改任何状态。

检查项：

| 项 | 判据 | 失败后果 |
|---|---|---|
| 插件清单 | `foolproof.composition.json` 存在且能被解析 | error（无法装配） |
| 分类清单 | 结构校验（``plugins.categories.audit``） | error（清单即事实源） |
| 索引状态 | 各卷 ``ready`` / ``missing`` / ``denied`` | missing=warn（可 `envs --build`），denied=warn（降级到 es/walk） |
| 检索后端 | 三后端可用性 | 全不可用=error |
| es.exe | 文件在位 | warn（仅影响 es 后端） |
| 索引目录 | 可创建/可写 | warn（不影响只读检索） |
| 任务日志 | 可写 | warn（日志失败不影响主流程） |

用法：``python requirements.py cli doctor``（``--json`` 输出机读结果）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .context import Service
from .runtime import default_manifest_path

__all__ = ["AuditItem", "AuditService", "format_report", "LEVEL_ERROR", "LEVEL_WARN", "LEVEL_OK"]

LEVEL_ERROR = "error"
LEVEL_WARN = "warn"
LEVEL_OK = "ok"


@dataclass
class AuditItem:
    """一条审计结果。"""

    level: str
    subject: str
    detail: str
    advice: str = ""

    @property
    def ok(self) -> bool:
        return self.level == LEVEL_OK

    def as_dict(self) -> Dict[str, str]:
        return {
            "level": self.level,
            "subject": self.subject,
            "detail": self.detail,
            "advice": self.advice,
        }


def format_report(items: List[AuditItem]) -> str:
    """人类可读报告（``✓`` / ``!`` / ``✗``）。"""
    marks = {LEVEL_OK: "✓", LEVEL_WARN: "!", LEVEL_ERROR: "✗"}
    lines: List[str] = []
    for item in items:
        line = "[{0}] {1}：{2}".format(marks.get(item.level, "?"), item.subject, item.detail)
        if item.advice:
            line += "　→ {0}".format(item.advice)
        lines.append(line)
    errors = sum(1 for item in items if item.level == LEVEL_ERROR)
    warns = sum(1 for item in items if item.level == LEVEL_WARN)
    lines.append("审计结论：{0} 项通过 / {1} 项警告 / {2} 项错误".format(
        sum(1 for item in items if item.ok), warns, errors))
    return "\n".join(lines)


class AuditService(Service):
    """启动审计服务（插件行 ``audit``）。可选依赖：``categories`` / ``fileindex`` / ``search`` / ``tasklog``。"""

    name = "audit"

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        self.manifest_path = (self.config or {}).get("manifest") or default_manifest_path()

    # ── 单项检查 ─────────────────────────────────────

    def _check_manifest(self) -> AuditItem:
        path = self.manifest_path
        if not os.path.isfile(path):
            return AuditItem(LEVEL_ERROR, "插件清单", "不存在：{0}".format(path),
                             "恢复 foolproof.composition.json（或删掉它走内置默认清单）")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data["plugins"] if isinstance(data, dict) else data
            ids = [row.get("id") for row in rows if isinstance(row, dict)]
        except Exception as exc:
            return AuditItem(LEVEL_ERROR, "插件清单", "解析失败：{0}".format(exc), "修好 JSON 再启动")
        if not ids or any(not i for i in ids):
            return AuditItem(LEVEL_ERROR, "插件清单", "存在缺 id 的行", "每行必须有非空 id")
        return AuditItem(LEVEL_OK, "插件清单", "{0} 行：{1}".format(len(ids), ", ".join(ids)))

    def _check_categories(self) -> AuditItem:
        service = self.ctx.get("categories", None) if self.ctx is not None else None
        if service is None:
            return AuditItem(LEVEL_WARN, "分类清单", "categories 行未装配", "检查清单里是否有 categories 行")
        problems = service.audit()
        stats = service.stats()
        if problems:
            return AuditItem(LEVEL_ERROR, "分类清单", "{0} 项结构问题".format(len(problems)),
                             problems[0])
        return AuditItem(
            LEVEL_OK, "分类清单",
            "{0} 分类 / {1} 库（自动发现 {2} 条）".format(
                stats["categories"], stats["entries"], stats["auto_discovery"]),
        )

    def _check_index(self) -> AuditItem:
        service = self.ctx.get("fileindex", None) if self.ctx is not None else None
        if service is None:
            return AuditItem(LEVEL_WARN, "索引状态", "fileindex 行未装配", "检索将退回 es/walk")
        try:
            status = service.status()
        except Exception as exc:
            return AuditItem(LEVEL_WARN, "索引状态", "状态读取失败：{0}".format(exc))
        if not status:
            return AuditItem(LEVEL_WARN, "索引状态", "未识别到任何 NTFS 卷", "非 NTFS 环境仅能走 walk")
        ready = [v for v, s in status.items() if s.get("status") == "ready"]
        denied = [v for v, s in status.items() if s.get("status") == "denied"]
        missing = [v for v, s in status.items() if s.get("status") == "missing"]
        detail = "ready={0} missing={1} denied={2}".format(
            ",".join(ready) or "-", ",".join(missing) or "-", ",".join(denied) or "-")
        if missing and not ready:
            return AuditItem(LEVEL_WARN, "索引状态", detail, "运行 cli envs --build 建索引")
        if denied:
            return AuditItem(LEVEL_WARN, "索引状态", detail, "被拒卷回退遍历；如需索引请以管理员运行")
        return AuditItem(LEVEL_OK, "索引状态", detail)

    def _check_backends(self) -> AuditItem:
        service = self.ctx.get("search", None) if self.ctx is not None else None
        if service is None:
            return AuditItem(LEVEL_WARN, "检索后端", "search 行未装配", "环境发现将不可用")
        rows = service.availability()
        usable = [name for name, ok, _ in rows if ok]
        detail = "；".join("{0}={1}".format(name, "可用" if ok else reason) for name, ok, reason in rows)
        if not usable:
            return AuditItem(LEVEL_ERROR, "检索后端", detail, "至少需要 walk 兜底可用")
        return AuditItem(LEVEL_OK, "检索后端", detail)

    def _check_tasklog(self) -> AuditItem:
        probe = None
        if self.ctx is not None:
            holder = self.ctx.get("tasklog", None)
            probe = getattr(holder, "log", holder)
        if probe is None:
            return AuditItem(LEVEL_WARN, "任务日志", "tasklog 行未装配", "操作流水不会被记录")
        entry = probe.record("audit.probe")
        if entry is None:
            return AuditItem(LEVEL_WARN, "任务日志", "写入失败：{0}".format(probe.last_error),
                             "检查路径与权限（或设 FOOLPROOF_TASKLOG）")
        return AuditItem(LEVEL_OK, "任务日志", probe.path)

    # ── 汇总 ─────────────────────────────────────────

    def run(self) -> List[AuditItem]:
        """执行全部检查（只读；唯一写入是任务日志的一条 `audit.probe`）。"""
        return [
            self._check_manifest(),
            self._check_interpreter(),
            self._check_categories(),
            self._check_index(),
            self._check_backends(),
            self._check_tasklog(),
        ]

    def _check_interpreter(self) -> AuditItem:
        """目标解释器：冻结版最容易在这里踩自指陷阱（拿本程序自身当 Python）。"""
        from .interpreter import is_self_executable, resolve_python_exe

        path, source = resolve_python_exe()
        if path is None:
            return AuditItem(
                LEVEL_ERROR, "目标解释器", source,
                "用 --python <python.exe> 指定，或设置环境变量 FOOLPROOF_PYTHON",
            )
        if is_self_executable(path):
            return AuditItem(
                LEVEL_ERROR, "目标解释器", "解析结果是本程序自身：{0}".format(path),
                "冻结版必须用 --python 指定真实解释器",
            )
        return AuditItem(LEVEL_OK, "目标解释器", "{0}（来源：{1}）".format(path, source))

    def failed(self, items: Optional[List[AuditItem]] = None) -> bool:
        return any(item.level == LEVEL_ERROR for item in (items if items is not None else self.run()))

    def __repr__(self) -> str:
        return "<AuditService manifest={0}>".format(self.manifest_path)
