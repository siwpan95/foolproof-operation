"""检索服务（插件行 ``search``）：多后端按可用性回退，并如实记录「谁答的」。

三个后端**并存**，不是替代关系（铁律：补优于删）：

| 后端 | 依赖 | 特点 |
|---|---|---|
| ``index`` | 本仓自研 USN 索引（``fileindex`` 服务） | 全盘任意深度、秒级；需建过索引 |
| ``es`` | 外部 Everything 进程 + 内置 ``es.exe``（MIT © voidtools） | 无需建索引；**Everything 未运行即不可用**（rc=8），且只覆盖 Everything 自己的索引范围 |
| ``walk`` | 无 | 永远可用；慢，受遍历深度/权限限制 |

回退语义：``find(..., backend="auto")`` 按上表顺序尝试，**第一个返回非空结果的胜出**；
每个后端的尝试与失败原因都记在结果里（``attempts``），所以「为什么没走索引」永远可查，
不会出现静默降级。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.context import Service

__all__ = [
    "SearchHit",
    "SearchResult",
    "SearchBackend",
    "IndexBackend",
    "EsBackend",
    "WalkBackend",
    "WalkRefused",
    "SearchService",
    "DEFAULT_SKIP_DIRS",
    "normalize_walk_root",
    "is_whole_drive_root",
]

#: 遍历后端默认跳过的目录名（系统/回收站/版本控制/包缓存等）。
DEFAULT_SKIP_DIRS = frozenset(
    {
        "$recycle.bin",
        "system volume information",
        "windows",
        "winsxs",
        "node_modules",
        ".git",
        "__pycache__",
        "appdata",
        "temp",
        "tmp",
    }
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ES_EXE = os.path.join(REPO_ROOT, "tools", "everything", "es.exe")


@dataclass
class SearchHit:
    """一条检索结果。``backend`` 标明来源，便于审计「这个结果是谁给的」。"""

    path: str
    name: str
    is_dir: bool = False
    backend: str = ""
    volume: Optional[str] = None
    index: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = os.path.basename(self.path.rstrip("\\/"))


@dataclass
class SearchResult:
    """一次检索的结果 + 溯源信息。"""

    hits: List[SearchHit] = field(default_factory=list)
    backend: str = ""
    attempts: List[Tuple[str, str]] = field(default_factory=list)
    missing_volumes: List[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.hits)

    def paths(self) -> List[str]:
        return [hit.path for hit in self.hits if hit.path]

    def explain(self) -> str:
        """人类可读的溯源摘要（谁尝试过、谁答的、为什么没走别的）。"""
        lines = ["后端尝试顺序："]
        for name, outcome in self.attempts:
            lines.append("  - {0}: {1}".format(name, outcome))
        lines.append("作答后端：{0}".format(self.backend or "（无）"))
        if self.missing_volumes:
            lines.append("缺索引的卷：{0}".format(", ".join(self.missing_volumes)))
        return "\n".join(lines)


class SearchBackend:
    """后端基类：``name`` + ``available()`` + ``find_names()``。"""

    name = "backend"

    def available(self) -> Tuple[bool, str]:
        """返回 ``(是否可用, 原因)``；原因会写进溯源信息。"""
        return True, "ok"

    def find_names(
        self,
        names: Sequence[str],
        *,
        roots: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> List[SearchHit]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<{0} name={1}>".format(type(self).__name__, self.name)


class IndexBackend(SearchBackend):
    """USN 索引后端：查 ``fileindex`` 服务。"""

    name = "index"

    def __init__(self, service: Any) -> None:
        self.service = service

    def available(self) -> Tuple[bool, str]:
        if self.service is None:
            return False, "索引服务未装配（fileindex 行缺失）"
        try:
            ready = [
                volume
                for volume, state in self.service.status().items()
                if state.get("status") == "ready"
            ]
        except Exception as exc:  # 服务异常不该让检索整条链崩掉
            return False, "索引服务状态不可读：{0}".format(exc)
        if not ready:
            return False, "尚无已建成的索引（可先运行 cli envs --build）"
        return True, "已就绪的卷：{0}".format(", ".join(ready))

    def find_names(
        self,
        names: Sequence[str],
        *,
        roots: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> List[SearchHit]:
        volumes = [vol for vol in (roots or [])] or None
        result = self.service.search(list(names), volumes=volumes, limit_per_name=limit)
        hits: List[SearchHit] = []
        for name, items in result.get("hits", {}).items():
            for item in items:
                hits.append(
                    SearchHit(
                        path=item.get("path") or "",
                        name=name,
                        is_dir=bool(item.get("is_dir")),
                        backend=self.name,
                        volume=item.get("volume"),
                        index=item.get("index"),
                    )
                )
        self.last_missing_volumes = list(result.get("missing", []))
        return hits


class EsBackend(SearchBackend):
    """Everything CLI 后端：调用内置 ``es.exe``（保持既有 ``cli es`` 能力不变）。"""

    name = "es"

    def __init__(self, es_exe: str = DEFAULT_ES_EXE, *, timeout: float = 20.0) -> None:
        self.es_exe = str(es_exe)
        self.timeout = float(timeout)
        self.last_error = ""

    def available(self) -> Tuple[bool, str]:
        if not os.path.isfile(self.es_exe):
            return False, "内置 es.exe 不存在：{0}".format(self.es_exe)
        if sys.platform != "win32":
            return False, "es.exe 仅在 Windows 可用"
        return True, "es.exe 在位（仍需 Everything 进程在运行才算真可用）"

    def find_names(
        self,
        names: Sequence[str],
        *,
        roots: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> List[SearchHit]:
        """逐名调用 ``es -p -n <limit> <name>``；``rc=8`` 即 Everything 未运行（如实记录）。"""
        hits: List[SearchHit] = []
        for name in names:
            command = [self.es_exe, "-p"]
            if limit:
                command += ["-n", str(int(limit))]
            command.append(name)
            try:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                )
            except Exception as exc:
                self.last_error = "es 调用失败：{0}".format(exc)
                return hits
            if proc.returncode == 8:
                self.last_error = "Everything 未运行（es rc=8）"
                return hits
            if proc.returncode != 0:
                self.last_error = "es rc={0}: {1}".format(proc.returncode, (proc.stderr or "").strip())
                return hits
            for line in (proc.stdout or "").splitlines():
                path = line.strip()
                if not path:
                    continue
                if roots and not any(_under(path, root) for root in roots):
                    continue
                hits.append(SearchHit(path=path, name=name, backend=self.name))
        return hits


class WalkRefused(ValueError):
    """拒绝整盘遍历（根是盘符根且未显式许可）。"""


def normalize_walk_root(root: str) -> str:
    """遍历根归一化：**``"G:"`` 必须变成 ``"G:\\\\"``**。

    否则 ``os.walk("G:")`` 会被解释为「G 盘的当前目录」——实测它静默扫了 cwd
    （0.03 秒返回 1 条命中），既不是整盘也不是用户以为的目录，**属于静默错误结果**。
    """
    text = str(root).strip()
    if not text:
        return text
    if len(text) == 2 and text[1] == ":":
        return text.upper() + "\\"
    return text


def is_whole_drive_root(root: str) -> bool:
    """是否为整盘根（``C:\\`` / ``C:/`` / ``C:``）。"""
    text = str(root).strip().rstrip("\\/")
    return len(text) == 2 and text[1] == ":"


class WalkBackend(SearchBackend):
    """目录遍历后端（兜底）：永远可用，但慢且受深度/权限限制。

    ``roots`` 为空表示「没有可遍历的根」（直接返回空，不猜）；
    根为**盘符根**（``G:\\``）时会遍历整个卷，属分钟~小时级操作，故默认**拒绝**，
    需显式 ``allow_whole_drives=True``（CLI ``--allow-whole-drives``）；
    另外总有 ``budget_seconds`` 墙钟预算兜底，超时即返回部分结果并**如实标注**。
    """

    name = "walk"

    def __init__(
        self,
        roots: Optional[Sequence[str]] = None,
        *,
        max_depth: int = 12,
        max_entries: int = 400_000,
        skip_dirs: Optional[Iterable[str]] = None,
        allow_whole_drives: bool = False,
        budget_seconds: float = 30.0,
    ) -> None:
        self.roots = [normalize_walk_root(r) for r in (roots or [])]
        self.max_depth = int(max_depth)
        self.max_entries = int(max_entries)
        self.skip_dirs = frozenset(d.lower() for d in (skip_dirs or DEFAULT_SKIP_DIRS))
        self.allow_whole_drives = bool(allow_whole_drives)
        self.budget_seconds = float(budget_seconds)
        self.truncated = False
        self.budget_exceeded = False
        self.last_error = ""
        self.scanned_dirs = 0

    def available(self) -> Tuple[bool, str]:
        if not self.roots:
            return True, "兜底后端（当前未配置遍历根，需 --volumes 指定目录）"
        return True, "兜底后端（无需外部依赖）"

    def find_names(
        self,
        names: Sequence[str],
        *,
        roots: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> List[SearchHit]:
        wanted = {name.lower(): name for name in names}
        search_roots = [normalize_walk_root(r) for r in (roots if roots is not None else self.roots)]
        if not search_roots:
            self.last_error = "未配置遍历根（请用 --volumes 指定目录）"
            return []
        blocked = [r for r in search_roots if is_whole_drive_root(r)]
        if blocked and not self.allow_whole_drives:
            raise WalkRefused(
                "拒绝整盘遍历：{0} 是盘符根（整卷遍历为分钟~小时级）。"
                "请给出具体目录，或显式许可整盘：CLI 加 --allow-whole-drives / "
                "配置 walk_allow_whole_drives=true".format(", ".join(blocked))
            )
        hits: List[SearchHit] = []
        per_name: Dict[str, int] = {}
        visited = 0
        self.truncated = False
        self.budget_exceeded = False
        self.scanned_dirs = 0
        self.last_error = ""
        started = time.monotonic()
        for root in search_roots:
            if not os.path.isdir(root):
                continue
            base_depth = root.rstrip("\\/").count(os.sep)
            for current, dirs, files in os.walk(root):
                visited += 1
                self.scanned_dirs += 1
                if time.monotonic() - started > self.budget_seconds:
                    self.budget_exceeded = True
                    self.last_error = "已达 {0:.0f}s 墙钟预算，结果可能不全（已扫 {1} 个目录）".format(
                        self.budget_seconds, self.scanned_dirs
                    )
                    return hits
                if visited > self.max_entries:
                    self.truncated = True
                    self.last_error = "已达条目上限 {0}，结果可能不全".format(self.max_entries)
                    return hits
                depth = current.rstrip("\\/").count(os.sep) - base_depth
                if depth >= self.max_depth:
                    dirs[:] = []
                dirs[:] = [d for d in dirs if d.lower() not in self.skip_dirs]
                for entry in list(dirs) + list(files):
                    key = entry.lower()
                    canonical = wanted.get(key)
                    if canonical is None:
                        continue
                    if limit is not None and per_name.get(canonical, 0) >= limit:
                        continue
                    path = os.path.join(current, entry)
                    hits.append(
                        SearchHit(
                            path=path,
                            name=canonical,
                            is_dir=entry in dirs,
                            backend=self.name,
                        )
                    )
                    per_name[canonical] = per_name.get(canonical, 0) + 1
        return hits


def _under(path: str, root: str) -> bool:
    """``path`` 是否位于 ``root`` 之下（大小写不敏感，容忍尾部斜杠）。"""
    p = os.path.normcase(os.path.abspath(path))
    r = os.path.normcase(os.path.abspath(root.rstrip("\\/") or root))
    return p == r or p.startswith(r + os.sep)


class SearchService(Service):
    """检索服务（插件行 ``search``）。

    配置：``es_exe``（es.exe 路径）、``walk_roots``（遍历后端根）、``walk_max_depth``、
    ``backend_order``（默认 ``["index", "es", "walk"]``）。
    """

    name = "search"

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        cfg: Dict[str, Any] = self.config
        self.order: List[str] = list(cfg.get("backend_order") or ["index", "es", "walk"])
        es_exe = cfg.get("es_exe") or DEFAULT_ES_EXE
        walk_roots = cfg.get("walk_roots") or []
        self._backends: Dict[str, SearchBackend] = {}
        # index 后端延迟绑定：fileindex 可能稍后才装（也允许压根没有）
        self.es_backend = EsBackend(es_exe)
        self.walk_backend = WalkBackend(
            walk_roots,
            max_depth=int(cfg.get("walk_max_depth") or 12),
            allow_whole_drives=bool(cfg.get("walk_allow_whole_drives")),
            budget_seconds=float(cfg.get("walk_budget_seconds") or 30.0),
        )
        self._backends["es"] = self.es_backend
        self._backends["walk"] = self.walk_backend

    # ── 后端注册表 ───────────────────────────────────

    def index_backend(self) -> Optional[IndexBackend]:
        """延迟取索引后端（``fileindex`` 服务此刻已装配才有）。"""
        service = self.ctx.get("fileindex", None) if self.ctx is not None else None
        if service is None:
            return None
        return IndexBackend(service)

    def backends(self) -> Dict[str, SearchBackend]:
        """当前可用后端（含延迟绑定的 index）。"""
        registry: Dict[str, SearchBackend] = dict(self._backends)
        index_backend = self.index_backend()
        if index_backend is not None:
            registry["index"] = index_backend
        return registry

    def register(self, name: str, backend: SearchBackend) -> None:
        """注册/替换后端（扩展点，供后续插件追加自己的检索源）。"""
        self._backends[name] = backend

    def availability(self) -> List[Tuple[str, bool, str]]:
        """按 ``order`` 列出每个后端的可用性；**未装配的后端也列出来**（如实说明缺什么）。"""
        registry = self.backends()
        rows: List[Tuple[str, bool, str]] = []
        for name in self.order:
            backend = registry.get(name)
            if backend is None:
                rows.append((name, False, "后端未装配（依赖服务缺失）"))
                continue
            ok, reason = backend.available()
            rows.append((name, ok, reason))
        return rows

    # ── 检索 ─────────────────────────────────────────

    def find(
        self,
        names: Sequence[str],
        *,
        backend: str = "auto",
        roots: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> SearchResult:
        """按名字检索。``backend="auto"`` 时按 ``order`` 回退，第一个有结果者胜出。"""
        if isinstance(names, str):
            names = [names]
        registry = self.backends()
        if backend != "auto":
            if backend not in registry:
                reason = (
                    "不可用：后端未装配（依赖服务缺失）"
                    if backend in self.order
                    else "未注册该后端（已注册：{0}）".format(", ".join(sorted(registry)))
                )
                return SearchResult(backend="", attempts=[(backend, reason)])
            candidates = [backend]
        else:
            # auto 模式遍历 order 的**全部**后端：未装配的也要记一条尝试，
            # 否则「为什么没走索引」在溯源里会消失（静默跳过）。
            candidates = list(self.order)
        result = SearchResult()
        for name in candidates:
            candidate = registry.get(name)
            if candidate is None:
                result.attempts.append((name, "不可用：后端未装配（依赖服务缺失）"))
                continue
            ok, reason = candidate.available()
            if not ok:
                result.attempts.append((name, "不可用：" + reason))
                continue
            try:
                hits = candidate.find_names(names, roots=roots, limit=limit)
            except WalkRefused as exc:
                result.attempts.append((name, "拒绝：{0}".format(exc)))
                continue
            except ValueError as exc:
                result.attempts.append((name, "拒绝：{0}".format(exc)))
                continue
            except Exception as exc:
                result.attempts.append((name, "查询异常：{0}".format(exc)))
                continue
            missing = list(getattr(candidate, "last_missing_volumes", []) or [])
            detail = "命中 {0} 条".format(len(hits))
            note = getattr(candidate, "last_error", "")
            if note:
                detail += "（{0}）".format(note)
            if not hits and missing:
                detail += "；缺索引的卷：{0}".format(", ".join(missing))
            result.attempts.append((name, detail))
            if hits:
                result.hits = hits
                result.backend = name
                result.missing_volumes = missing
                return result
        return result

    def find_paths(self, names: Sequence[str], **kwargs: Any) -> List[str]:
        return self.find(names, **kwargs).paths()

    def __repr__(self) -> str:
        return "<SearchService order={0}>".format(self.order)
