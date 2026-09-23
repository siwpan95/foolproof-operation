"""面板模型（阶段 C）：把「任务日志」与「环境发现」两个面板的**数据逻辑**与 tkinter 分离。

- ``TaskLogModel``：读 JSONL 任务日志 → 过滤（事件名 / 只看失败 / 条数上限）→ 行文本与统计；
- ``EnvPanelModel``：环境发现（走 scanner 服务）→ 行文本；索引状态；触发建库/增量同步。

GUI 只负责「把行文本放进控件、把按钮接到这里的方法」，因此面板逻辑可被单测覆盖，
且**建库/同步跑在后台线程**（面板里用 ``worker`` 参数注入线程启动方式，测试里同步执行）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = ["TaskLogModel", "EnvPanelModel", "TaskLogRow", "EnvRow"]


@dataclass
class TaskLogRow:
    """任务日志的一行（供表格/文本控件渲染）。"""

    ts: str
    event: str
    detail: str
    failed: bool = False

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.ts, self.event, self.detail)


@dataclass
class EnvRow:
    """环境发现的一行。"""

    kind: str
    root: str
    version: str
    python_exe: str
    backend: str = ""
    valid: Optional[bool] = None

    def as_tuple(self) -> Tuple[str, str, str]:
        return (self.kind, self.root, self.version)


class TaskLogModel:
    """任务日志面板模型。"""

    def __init__(self, service: Any) -> None:
        self.service = service

    def rows(
        self,
        *,
        limit: int = 200,
        event_filter: str = "",
        only_failures: bool = False,
    ) -> List[TaskLogRow]:
        """读事件 → 过滤 → 行对象（新的在后，与 JSONL 顺序一致）。"""
        events = self.service.events(limit=None) or []
        needle = (event_filter or "").strip().lower()
        rows: List[TaskLogRow] = []
        for entry in events:
            if not isinstance(entry, dict):
                continue
            event = str(entry.get("event", ""))
            if needle and needle not in event.lower():
                continue
            failed = bool(entry.get("failed")) or entry.get("ok") is False
            if only_failures and not failed:
                continue
            rows.append(
                TaskLogRow(
                    ts=str(entry.get("ts", "")),
                    event=event,
                    detail=self._detail(entry),
                    failed=failed,
                )
            )
        if limit and limit > 0:
            rows = rows[-limit:]
        return rows

    @staticmethod
    def _detail(entry: Dict[str, Any]) -> str:
        parts = []
        for key, value in entry.items():
            if key in ("ts", "event"):
                continue
            parts.append("{0}={1}".format(key, value))
        return "  ".join(parts)

    def summary(self) -> Dict[str, Any]:
        return self.service.summary() or {}

    def header(self) -> str:
        """面板顶部的一句话摘要。"""
        info = self.summary()
        if not info or not info.get("total"):
            return "暂无记录（安装/卸载/检查/建库等操作会自动记账）"
        return "共 {0} 条（首 {1} / 末 {2}），失败计数 {3}".format(
            info.get("total"), info.get("first"), info.get("last"), info.get("failures")
        )

    def event_names(self) -> List[str]:
        names = sorted((self.summary().get("events") or {}).keys())
        return names

    def clear(self) -> None:
        self.service.log.clear()


class EnvPanelModel:
    """环境发现面板模型（发现 + 索引状态 + 建库/同步）。"""

    def __init__(self, scanner: Any, index: Any = None, *, worker: Optional[Callable] = None) -> None:
        self.scanner = scanner
        self.index = index
        self.worker = worker or (lambda fn, *a, **kw: fn(*a, **kw))

    # ── 发现 ─────────────────────────────────────────

    def discover(
        self, *, backend: str = "auto", volumes: Optional[Sequence[str]] = None, deep: bool = False
    ) -> List[EnvRow]:
        """发现环境；返回行对象（来源后端一并带出，便于界面标注「谁答的」）。"""
        envs = self.scanner.discover(backend=backend, roots=list(volumes or []) or None, deep=deep)
        source = ""
        result = getattr(self.scanner, "last_result", None)
        if result is not None:
            source = getattr(result, "backend", "") or ""
        rows: List[EnvRow] = []
        for env in envs or []:
            rows.append(
                EnvRow(
                    kind=getattr(env, "kind", "?"),
                    root=getattr(env, "root", ""),
                    version=getattr(env, "version", None) or "?",
                    python_exe=getattr(env, "python_exe", None) or "（未找到解释器）",
                    backend=source,
                    valid=getattr(env, "valid", None),
                )
            )
        return rows

    def discover_header(self, *, backend: str = "auto") -> str:
        """发现结果的溯源摘要（含各后端尝试原因）。"""
        result = getattr(self.scanner, "last_result", None)
        if result is None:
            return "来源：旧口径（legacy）"
        lines = ["来源后端：{0}".format(getattr(result, "backend", "") or "（无）")]
        for name, outcome in getattr(result, "attempts", []) or []:
            lines.append("  · {0}: {1}".format(name, outcome))
        missing = getattr(result, "missing_volumes", None)
        if missing:
            lines.append("缺索引的卷：{0}".format(", ".join(missing)))
        return "\n".join(lines)

    # ── 索引 ─────────────────────────────────────────

    def index_status(self) -> List[Tuple[str, str, str]]:
        """``[(卷, 状态, 说明)]``；无索引服务时返回空列表。"""
        if self.index is None:
            return []
        rows: List[Tuple[str, str, str]] = []
        for volume, state in sorted((self.index.status() or {}).items()):
            rows.append(
                (volume, str(state.get("status", "?")), str(state.get("detail", "")) or "-")
            )
        return rows

    def index_summary(self) -> str:
        rows = self.index_status()
        if not rows:
            return "索引服务未装配"
        ready = [v for v, s, _ in rows if s == "ready"]
        missing = [v for v, s, _ in rows if s == "missing"]
        denied = [v for v, s, _ in rows if s == "denied"]
        return "已建索引：{0}｜未建：{1}｜被拒：{2}".format(
            ",".join(ready) or "-", ",".join(missing) or "-", ",".join(denied) or "-"
        )

    def build(self, volume: str, *, force: bool = False, async_run: bool = True) -> Any:
        """触发建库（默认交给注入的 worker，即后台线程）。"""
        if self.index is None:
            raise RuntimeError("索引服务未装配")
        if async_run:
            return self.worker(self.index.build, volume, force=force)
        return self.index.build(volume, force=force)

    def sync(self, volume: str, *, async_run: bool = True) -> Any:
        """触发增量同步（默认后台线程）。"""
        if self.index is None:
            raise RuntimeError("索引服务未装配")
        if async_run:
            return self.worker(self.index.sync, volume)
        return self.index.sync(volume)
