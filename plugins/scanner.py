"""环境发现插件（行 ``scanner``）：证据 → 候选环境 → 可选深验证。

**证据优先，不靠目录名关键字**（这正是旧实现的漏点）：

| 证据 | 判定 | 环境根 |
|---|---|---|
| ``pyvenv.cfg`` | venv（内容给出 version/home/include-system-site-packages） | 该文件所在目录 |
| ``conda-meta``（目录） | conda 环境 | 其父目录 |
| ``python.exe`` + ``python*._pth`` | 嵌入式分发包（embed） | 该 exe 所在目录 |
| ``python.exe``/``pythonw.exe`` | 独立/系统安装 | exe 所在目录（位于 ``Scripts``/``bin`` 时上提一级） |
| ``site-packages``（目录） | 归属证据（不单独成为环境） | 就近挂到所属环境 |

**旧口径保留**：``legacy_walk()`` 是原 GUI ``_find_python_installations()`` 的等价实现
（各盘顶层 + 一层子目录按目录名关键字匹配）。它慢且漏深层 venv，但作为兜底与 A/B 对照**必须留着**
（铁律：补优于删）——``discover(backend="legacy")`` 即用它。

深验证（``validate(env, deep=True)``）才真跑解释器（``python --version``、``pip --version``），
因为对 200+ 候选逐个起进程代价高；默认只用文件证据。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.context import Service

__all__ = ["PythonEnvironment", "ScannerService", "EVIDENCE_NAMES", "parse_pyvenv_cfg"]

#: 环境发现的证据文件名（与 ``index.probe.DEFAULT_MATCHES`` 保持一致口径）。
EVIDENCE_NAMES = ("python.exe", "pythonw.exe", "pyvenv.cfg", "conda-meta", "site-packages")

_PTH_RE = re.compile(r"^python\d+\._pth$", re.IGNORECASE)
_SCRIPT_DIRS = ("scripts", "bin")
#: CPython 自带的 venv 模板资产（``Lib/venv/scripts/nt/python.exe`` 之类）——
#: 它们**不是**可用环境，但会被证据检索命中，故显式标记而非静默丢弃。
_TEMPLATE_PARTS = (("venv", "scripts", "nt"), ("venv", "scripts", "posix"))


def _is_template_path(path: str) -> bool:
    """路径是否落在 venv 模板资产里（``…\\venv\\scripts\\nt|posix\\…``）。"""
    parts = [p.lower() for p in path.replace("/", "\\").split("\\") if p]
    for pattern in _TEMPLATE_PARTS:
        for i in range(len(parts) - len(pattern) + 1):
            if tuple(parts[i : i + len(pattern)]) == pattern:
                return True
    return False


def parse_pyvenv_cfg(path: str) -> Dict[str, str]:
    """解析 ``pyvenv.cfg``（``key = value`` 行）；读取失败返回空字典（不抛错）。"""
    data: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                data[key.strip().lower()] = value.strip()
    except OSError:
        return {}
    return data


@dataclass
class PythonEnvironment:
    """一个 Python 环境候选（含证据与来源后端）。"""

    root: str
    kind: str = "standalone"          # venv / conda / embed / standalone / legacy
    python_exe: Optional[str] = None
    pythonw_exe: Optional[str] = None
    site_packages: Optional[str] = None
    version: Optional[str] = None
    base: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    backend: str = ""
    valid: Optional[bool] = None      # 深验证结果（None = 未验证）
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "kind": self.kind,
            "python_exe": self.python_exe,
            "pythonw_exe": self.pythonw_exe,
            "site_packages": self.site_packages,
            "version": self.version,
            "base": self.base,
            "evidence": list(self.evidence),
            "backend": self.backend,
            "valid": self.valid,
            "detail": self.detail,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<PythonEnvironment {0} kind={1} version={2}>".format(
            self.root, self.kind, self.version
        )


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path.rstrip("\\/") or path))


class ScannerService(Service):
    """环境发现服务（插件行 ``scanner``）。

    必需依赖 ``search``（内核 ``inject`` 保证：search 没装好就不会装配本行，fail loud）。
    配置：``volumes``（检索根/卷）、``limit_per_name``、``deep``（发现时即深验证，默认关）。
    """

    name = "scanner"
    inject: Sequence[str] = ("search",)

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        cfg: Dict[str, Any] = self.config
        self.volumes = [str(v) for v in (cfg.get("volumes") or [])]
        self.limit_per_name = cfg.get("limit_per_name")
        self.deep = bool(cfg.get("deep"))
        self.last_result = None

    # ── 主入口 ───────────────────────────────────────

    def discover(
        self,
        *,
        backend: str = "auto",
        roots: Optional[Sequence[str]] = None,
        names: Sequence[str] = EVIDENCE_NAMES,
        deep: Optional[bool] = None,
    ) -> List[PythonEnvironment]:
        """检索证据 → 聚合环境候选（按根路径去重、排序）。

        ``backend="legacy"`` 走旧口径（``legacy_walk``）；其余交检索服务按可用性回退。
        """
        roots = list(roots or self.volumes) or None
        if backend == "legacy":
            return self.legacy_walk(roots=roots)
        search = self.ctx.get("search")
        result = search.find(list(names), backend=backend, roots=roots, limit=self.limit_per_name)
        self.last_result = result
        environments = self.group(result.paths(), backend=result.backend)
        if deep if deep is not None else self.deep:
            for environment in environments:
                self.validate(environment, deep=True)
        return environments

    # ── 证据 → 环境聚合（纯函数，便于单测）────────────

    def group(self, paths: Iterable[str], *, backend: str = "", exists=os.path.isfile) -> List[PythonEnvironment]:
        """把证据路径聚合成环境候选。

        ``exists`` 可注入（测试/无盘环境），默认查磁盘。**只做文件证据判定，不起进程。**
        """
        by_root: Dict[str, PythonEnvironment] = {}

        def env_for(root: str) -> PythonEnvironment:
            key = _norm(root)
            environment = by_root.get(key)
            if environment is None:
                environment = PythonEnvironment(root=root, backend=backend)
                by_root[key] = environment
            return environment

        pyvenv_roots: List[str] = []
        attachments: List[str] = []  # conda-meta / site-packages 目录
        for raw in paths:
            path = raw.rstrip("\\/") if raw else raw
            if not path:
                continue
            name = os.path.basename(path).lower()
            if name == "pyvenv.cfg":
                pyvenv_roots.append(os.path.dirname(path))
            elif name in ("conda-meta", "site-packages"):
                attachments.append(path)
            elif name in ("python.exe", "pythonw.exe"):
                env_for(self._root_of_python(os.path.dirname(path), path)).evidence.append(path)

        # venv：pyvenv.cfg 处即环境根，并读出版本与基座
        for root in pyvenv_roots:
            environment = env_for(root)
            environment.kind = "venv"
            cfg_path = os.path.join(root, "pyvenv.cfg")
            environment.evidence.append(cfg_path)
            info = parse_pyvenv_cfg(cfg_path)
            environment.version = info.get("version") or environment.version
            environment.base = info.get("home") or environment.base
            for candidate in (
                os.path.join(root, "Scripts", "python.exe"),
                os.path.join(root, "bin", "python"),
            ):
                if environment.python_exe is None and exists(candidate):
                    environment.python_exe = candidate
        # conda：conda-meta 的父目录
        for path in attachments:
            if os.path.basename(path).lower() == "conda-meta":
                root = os.path.dirname(path)
                environment = env_for(root)
                environment.kind = "conda"
                environment.evidence.append(path)
        # site-packages 归位（就近挂到所属环境）
        for path in attachments:
            if os.path.basename(path).lower() != "site-packages":
                continue
            owner = self._owner_root(path, by_root)
            if owner is not None:
                owner.site_packages = owner.site_packages or path
                owner.evidence.append(path)
        for environment in by_root.values():
            self._fill_exes(environment, exists=exists)
            if environment.kind == "standalone" and environment.python_exe:
                directory = os.path.dirname(environment.python_exe)
                if any(_PTH_RE.match(entry) for entry in self._listdir(directory)):
                    environment.kind = "embed"
            if _is_template_path(environment.root):
                environment.valid = False
                environment.detail = "疑似 CPython venv 模板资产（Lib/venv/scripts/nt|posix），非独立环境"
        return sorted(by_root.values(), key=lambda env: _norm(env.root))

    def _root_of_python(self, directory: str, exe: str) -> str:
        """python.exe 所属环境根：位于 ``Scripts``/``bin`` 时上提一级。"""
        if os.path.basename(directory).lower() in _SCRIPT_DIRS:
            return os.path.dirname(directory)
        return directory

    def _owner_root(self, path: str, by_root: Dict[str, PythonEnvironment]) -> Optional[PythonEnvironment]:
        """site-packages 归属于哪个已知环境根（取最长前缀匹配）。"""
        target = _norm(path)
        best: Optional[PythonEnvironment] = None
        best_len = -1
        for environment in by_root.values():
            root = _norm(environment.root)
            if target == root or target.startswith(root + os.sep):
                if len(root) > best_len:
                    best, best_len = environment, len(root)
        return best

    def _fill_exes(self, environment: PythonEnvironment, *, exists=os.path.isfile) -> None:
        root = environment.root
        if environment.python_exe is None:
            for candidate in (
                os.path.join(root, "python.exe"),
                os.path.join(root, "Scripts", "python.exe"),
                os.path.join(root, "bin", "python"),
            ):
                if exists(candidate):
                    environment.python_exe = candidate
                    break
        if environment.pythonw_exe is None:
            for candidate in (
                os.path.join(root, "pythonw.exe"),
                os.path.join(root, "Scripts", "pythonw.exe"),
            ):
                if exists(candidate):
                    environment.pythonw_exe = candidate
                    break

    @staticmethod
    def _listdir(path: str) -> List[str]:
        try:
            return os.listdir(path)
        except OSError:
            return []

    # ── 深验证 ───────────────────────────────────────

    def validate(self, environment: PythonEnvironment, *, deep: bool = False) -> PythonEnvironment:
        """校验环境：``deep=False`` 只查文件证据；``deep=True`` 真跑解释器。"""
        if not environment.evidence and not environment.python_exe:
            environment.valid = False
            environment.detail = "无证据"
            return environment
        if environment.python_exe is None:
            environment.valid = False
            environment.detail = "未找到解释器可执行文件"
            return environment
        if not os.path.isfile(environment.python_exe):
            environment.valid = False
            environment.detail = "解释器路径不存在：{0}".format(environment.python_exe)
            return environment
        if not deep:
            environment.valid = None
            environment.detail = "文件证据通过（未深验证）"
            return environment
        try:
            proc = subprocess.run(
                [environment.python_exe, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except Exception as exc:
            environment.valid = False
            environment.detail = "解释器执行失败：{0}".format(exc)
            return environment
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        environment.valid = proc.returncode == 0 and "Python" in text
        environment.version = environment.version or (text.split()[-1] if text else None)
        environment.detail = text or "无输出"
        return environment

    # ── 旧口径（保留，兜底与 A/B 对照）────────────────

    def legacy_walk(self, *, roots: Optional[Sequence[str]] = None) -> List[PythonEnvironment]:
        """原 GUI ``_find_python_installations()`` 的等价实现（各盘顶层 + 一层子目录按名匹配）。

        刻意**不改进**它：它是「旧能力」的基线，用来对照索引后端的收益；
        真正要覆盖深层 venv 请用 ``discover(backend="auto")``。
        """
        keywords = ("python", "anaconda", "miniconda", "venv", ".venv")
        roots = list(roots or self.volumes) or ["C:/", "D:/", "E:/", "F:/", "G:/"]
        found: List[str] = []
        seen = set()
        for root in roots:
            base = root.rstrip("\\/") or root
            if not os.path.isdir(base):
                continue
            try:
                entries = list(os.scandir(base))
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir():
                    continue
                name = entry.name.lower()
                if any(keyword in name for keyword in keywords):
                    self._remember(found, seen, entry.path)
                if entry.name in (
                    "Windows",
                    "Program Files",
                    "Program Files (x86)",
                    "ProgramData",
                    "$Recycle.Bin",
                    "System Volume Information",
                ):
                    continue
                try:
                    children = list(os.scandir(entry.path))
                except OSError:
                    continue
                for child in children:
                    if child.is_dir() and any(k in child.name.lower() for k in keywords):
                        self._remember(found, seen, child.path)
        environments: List[PythonEnvironment] = []
        for path in sorted(found, key=_norm):
            environment = PythonEnvironment(root=path, kind="legacy", backend="legacy")
            self._fill_exes(environment, exists=os.path.isfile)
            environment.evidence.append("目录名启发式（旧口径）")
            environments.append(environment)
        return environments

    @staticmethod
    def _remember(found: List[str], seen: set, path: str) -> None:
        key = _norm(path)
        if key not in seen:
            seen.add(key)
            found.append(path)

    def __repr__(self) -> str:
        return "<ScannerService volumes={0} deep={1}>".format(self.volumes, self.deep)
