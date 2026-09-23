"""requirements.txt 插件（行 ``reqtxt``）：解析 + 结构化安装结果。

解析规则**逐字迁移**自 ``InstallerApp._parse_requirements_txt``：

- 按 ``utf-8-sig → utf-8 → gbk`` 依次尝试解码，全失败再以 ``errors="ignore"`` 兜底；
- 跳过空行、``#`` 注释行、``-`` 开头的选项行（``-r`` / ``-e`` / ``--index-url`` …）；
- 跳过误粘贴的 shell 命令（``pip install …`` / ``python -m pip install …``）；
- 去行内注释时要求 ``#`` **前有空白**，避免误伤 URL 里的 ``#sha256=…``；
- 命中 ``包名[extras]`` 后返回 ``(包名, 传给 pip 的原始规格)``。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.context import Service

__all__ = ["ReqTxtService", "parse_requirements_txt", "parse_requirements_text"]

_COMMAND_RE = re.compile(r"^(pip|pip3|python|py)(\.exe)?\s+(-m\s+pip\s+)?install\s+", re.I)
_PKG_RE = re.compile(r"^([a-zA-Z0-9._-]+(?:\[[a-zA-Z0-9._,-]+\])?)")
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk")


def parse_requirements_text(text: str) -> List[Tuple[str, str]]:
    """解析 requirements.txt 的**文本**，返回 ``[(包名, 安装规格), ...]``。"""
    packages: List[Tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue
        # 跳过选项行（-r / -e / --index-url / --extra-index-url 等）
        if line.startswith("-"):
            continue
        # 跳过误粘贴的 shell 命令
        if _COMMAND_RE.match(line):
            continue
        # 去掉行内注释（# 前必须有空白，避免误伤 URL 中的 #sha256=...）
        req = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not req:
            continue
        # 匹配包名部分（字母数字._-），后跟可选的 [extras]
        match = _PKG_RE.match(req)
        if match:
            packages.append((match.group(1), req))
    return packages


def parse_requirements_txt(filepath: str) -> List[Tuple[str, str]]:
    """解析 requirements.txt 文件（编码按 ``utf-8-sig → utf-8 → gbk`` 回退）。

    文件不可读时返回空列表（与迁移前一致：**不抛异常**）。
    """
    packages: List[Tuple[str, str]] = []
    text: Optional[str] = None
    for enc in _ENCODINGS:
        try:
            with open(filepath, "r", encoding=enc) as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
        except OSError:
            return packages
    if text is None:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            return packages
    return parse_requirements_text(text)


class ReqTxtService(Service):
    """requirements.txt 服务（插件行 ``reqtxt``）。

    只做解析与「按规格逐个安装」的编排；界面与日志仍由调用方负责（GUI/CLI 各自渲染）。
    """

    name = "reqtxt"

    def parse(self, filepath: str) -> List[Tuple[str, str]]:
        return parse_requirements_txt(filepath)

    def parse_text(self, text: str) -> List[Tuple[str, str]]:
        return parse_requirements_text(text)

    def install_from_file(
        self,
        filepath: str,
        *,
        pip: Any = None,
        python_exe: Optional[str] = None,
        on_event: Any = None,
    ) -> Dict[str, Any]:
        """按文件逐条安装（``pip`` 缺省取内核 ``pip`` 服务）。

        返回 ``{"total", "installed", "failed", "results": [(包名, 规格, rc, stderr), ...]}``；
        ``on_event(kind, payload)`` 可选，用于界面逐步刷新（kind: ``start`` / ``item`` / ``done``）。
        """
        if pip is None:
            pip = self.ctx.get("pip", None) if self.ctx is not None else None
        packages = self.parse(filepath)
        result: Dict[str, Any] = {
            "total": len(packages),
            "installed": 0,
            "failed": 0,
            "results": [],
        }
        if on_event:
            on_event("start", {"file": filepath, "total": len(packages)})
        for name, spec in packages:
            rc, err = pip.install([spec], python_exe=python_exe) if pip is not None else (-1, "pip 服务未装配")
            result["results"].append((name, spec, rc, err))
            if rc == 0:
                result["installed"] += 1
            else:
                result["failed"] += 1
            if on_event:
                on_event("item", {"name": name, "spec": spec, "rc": rc, "stderr": err})
        if on_event:
            on_event("done", result)
        return result

    def __repr__(self) -> str:
        return "<ReqTxtService>"
