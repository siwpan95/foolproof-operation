"""任务日志（JSONL）：安装/卸载/检查/建库/发现等操作的事件流水。

与工作区既有口径同构（crawl_hub 的 ``tasks.log``、audio_pipeline 的 ``pipelog.py``、
引擎的 ``WorldLog``）：**一行一个 JSON 事件**，便于 grep、便于给 agent/审计脚本消费。

- 默认落在**用户级目录**（``%LOCALAPPDATA%\\foolproof-operation\\logs\\tasks.jsonl``），
  可用 ``FOOLPROOF_TASKLOG`` 覆盖；刻意不写仓库内（日志是运行期产物，不入版本控制）。
- **日志失败绝不拖垮主流程**：写入异常静默吞掉（与内核 ``dispose`` 的容错口径一致）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .context import Service

__all__ = [
    "TaskLog",
    "default_tasklog_path",
    "read_events",
    "summarize",
    "tasklog_plugin",
]

LOG_FILE = "tasks.jsonl"


def default_tasklog_path() -> str:
    """默认日志路径：``FOOLPROOF_TASKLOG`` → ``%LOCALAPPDATA%`` → 用户主目录。"""
    override = os.environ.get("FOOLPROOF_TASKLOG")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "foolproof-operation", "logs", LOG_FILE)


class TaskLog:
    """JSONL 任务日志（可禁用；禁用与写入失败都不影响调用方）。"""

    def __init__(self, path: Optional[str] = None, *, enabled: bool = True) -> None:
        self.path = path or default_tasklog_path()
        self.enabled = bool(enabled)
        self.last_error: Optional[str] = None
        self.written = 0

    def record(self, event: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """追加一条事件；返回写入的字典（未写成功返回 None）。"""
        if not self.enabled:
            return None
        entry: Dict[str, Any] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event}
        entry.update(fields)
        line = json.dumps(entry, ensure_ascii=False)
        try:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:  # 日志写不进去不该中断安装/扫描
            self.last_error = str(exc)
            return None
        self.written += 1
        return entry

    def events(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        entries = read_events(self.path, limit=limit)
        return entries

    def summary(self, *, limit: Optional[int] = None) -> Dict[str, Any]:
        return summarize(self.events(limit=limit))

    def clear(self) -> None:
        """清空日志（保留文件）。"""
        try:
            open(self.path, "w", encoding="utf-8").close()
        except Exception as exc:
            self.last_error = str(exc)

    def __repr__(self) -> str:
        return "<TaskLog path={0} enabled={1} written={2}>".format(
            self.path, self.enabled, self.written
        )


def read_events(path: Optional[str] = None, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """读回 JSONL 事件（坏行跳过，不抛异常）；``limit`` 取**最后** N 条。"""
    target = path or default_tasklog_path()
    if not os.path.isfile(target):
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    entries.append(row)
    except OSError:
        return []
    if limit is not None and limit >= 0:
        return entries[-limit:]
    return entries


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总事件：总数、按事件名计数、失败数、首末时间与最近一条。"""
    counts: Dict[str, int] = {}
    failures = 0
    for row in entries:
        name = str(row.get("event", "?"))
        counts[name] = counts.get(name, 0) + 1
        if row.get("failed"):
            failures += int(row["failed"])
        elif row.get("ok") is False:
            failures += 1
    return {
        "total": len(entries),
        "events": counts,
        "failures": failures,
        "first": entries[0].get("ts") if entries else None,
        "last": entries[-1].get("ts") if entries else None,
        "latest": entries[-1] if entries else None,
    }


def tasklog_plugin(ctx: Any, config: Any = None) -> None:
    """插件行 ``tasklog``：把任务日志注册为 ``tasklog`` 服务。"""
    cfg = config or {}
    log = TaskLog(cfg.get("path"), enabled=cfg.get("enabled", True))
    ctx.provide("tasklog", log)


class TaskLogService(Service):
    """任务日志服务（类式形态，供需要 ``inject`` 声明依赖的插件使用）。"""

    name = "tasklog"

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        self.log = TaskLog((self.config or {}).get("path"), enabled=(self.config or {}).get("enabled", True))

    def record(self, event: str, **fields: Any) -> Optional[Dict[str, Any]]:
        return self.log.record(event, **fields)

    def events(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.log.events(limit=limit)

    def summary(self, *, limit: Optional[int] = None) -> Dict[str, Any]:
        return self.log.summary(limit=limit)
