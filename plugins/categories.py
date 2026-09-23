"""分类清单插件（行 ``categories``）：**配置即清单 + 启动审计**。

`requirements_categories.json` 是本工具的事实源（24 个功能分类 / 761 个库 + 「🔍 自动发现」桶）。
本模块把原先散在 `requirements.py` 与 `cli.py` 里的清单逻辑收拢成插件：

- 纯函数：``load_categories`` / ``audit`` / ``all_packages`` / ``find_category`` /
  ``resolve_packages`` / ``target_of`` / ``pip_query_name`` / ``is_local_spec`` / ``split_spec``
  （**语义与迁移前逐字保持一致**，含 `SystemExit` 的措辞 —— CLI 行为零变化由它们保证）；
- 服务：``CategoriesService``（name ``categories``），供插件行装配与其它插件注入。

`audit()` 是「配置即清单」的 fail-loud 面：清单结构非法时**装配即拒绝**，而不是等运行到一半才炸。
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.context import Service

__all__ = [
    "CategoriesError",
    "CategoriesService",
    "load_categories",
    "audit",
    "all_packages",
    "find_category",
    "resolve_packages",
    "target_of",
    "pip_query_name",
    "is_local_spec",
    "split_spec",
    "default_categories_path",
    "AUTO_DISCOVERY_KEY",
]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATEGORIES_FILE = "requirements_categories.json"
#: 「自动发现」桶的键（其条目是待人工归类的候选，不计入精选库数）。
AUTO_DISCOVERY_KEY = "🔍 自动发现"

#: 本地路径 / pip 选项形式的 value（迁移自 cli.py，逐字一致）。
_LOCAL_SPEC = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|\.\.?[\\/]|-)")

Categories = Dict[str, Dict[str, str]]


class CategoriesError(ValueError):
    """分类清单非法（结构错误 / 文件缺失 / JSON 解析失败）。"""


def default_categories_path() -> str:
    return os.path.join(REPO_ROOT, CATEGORIES_FILE)


def load_categories(path: Optional[str] = None) -> Categories:
    """读取并校验分类清单；失败抛 ``CategoriesError``（fail loud）。"""
    target = path or default_categories_path()
    if not os.path.isfile(target):
        raise CategoriesError("分类清单不存在：{0}".format(target))
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise CategoriesError("分类清单 JSON 解析失败：{0}".format(exc)) from exc
    except OSError as exc:
        raise CategoriesError("分类清单读取失败：{0}".format(exc)) from exc
    problems = audit(data)
    if problems:
        raise CategoriesError(
            "分类清单结构非法（{0} 项）：{1}".format(
                len(problems), "；".join(problems[:5]) + ("…" if len(problems) > 5 else "")
            )
        )
    return data


def audit(categories: Any) -> List[str]:
    """审计清单结构，返回问题清单（空列表 = 通过）。

    判据：顶层为对象；每个分类为对象；每个库名为非空字符串；每个 value 为非空字符串。
    """
    problems: List[str] = []
    if not isinstance(categories, dict):
        return ["清单顶层需为对象（形如 {\"分类\": {\"库名\": \"安装目标\"}}）"]
    for category, libs in categories.items():
        if not isinstance(category, str) or not category.strip():
            problems.append("存在空分类名")
            continue
        if not isinstance(libs, dict):
            problems.append("分类「{0}」需为对象".format(category))
            continue
        if not libs:
            problems.append("分类「{0}」为空".format(category))
        for name, value in libs.items():
            if not isinstance(name, str) or not name.strip():
                problems.append("分类「{0}」存在空库名".format(category))
            if not isinstance(value, str) or not value.strip():
                problems.append("分类「{0}」的「{1}」value 需为非空字符串".format(category, name))
    return problems


# ── 纯函数（与迁移前 cli.py 逐字一致）────────────────────────────────────


def is_local_spec(pkg: str) -> bool:
    """value 是否为本地路径 / pip 选项形式（而非 PyPI 包名）。"""
    return bool(_LOCAL_SPEC.match(pkg or ""))


def target_of(categories: Categories, name: str) -> str:
    """库名 → pip 安装目标（value）；非清单项原样返回。"""
    for libs in categories.values():
        if name in libs:
            return libs[name]
    return name


def pip_query_name(name: str, pkg: str) -> str:
    """pip show / pip uninstall 需要的包名：去掉 extras；本地路径回落显示名。"""
    return name if is_local_spec(pkg) else pkg.split("[")[0]


def split_spec(pkg: str) -> List[str]:
    """把分类 value 拆成 pip 参数列表，支持 `-e <路径>` 这类选项。

        'requests'               -> ['requests']
        'fastapi[all]'           -> ['fastapi[all]']
        '-e G:/repos/an-engine'  -> ['-e', 'G:/repos/an-engine']

    shlex 的两种模式各有短板，故按输入特征选择：
      - 含引号 → posix=True：引号语义正确，且引号内的反斜杠会被保留
      - 无引号但含反斜杠 → posix=False：否则 'G:\\repos' 会被吃掉分隔符且**不报错**
      - 其余 → posix=True
    路径建议一律写正斜杠，可完全绕开这个坑。
    """
    if not pkg:
        return []
    posix = not ("\\" in pkg and '"' not in pkg)
    try:
        return shlex.split(pkg, posix=posix) or [pkg]
    except ValueError:          # 引号不配对等异常输入，退化为整体传参
        return [pkg]


def all_packages(categories: Categories) -> List[str]:
    """清单内全部库名（去重保序）。"""
    out: List[str] = []
    for pkgs in categories.values():
        out.extend(pkgs)
    # 去重保序
    seen = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def find_category(categories: Categories, name: str) -> str:
    """按精确键或唯一子串匹配分类（分类名带 emoji 前缀，允许用户省略）。"""
    if name in categories:
        return name
    hits = [k for k in categories if name in k]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit("未知分类：{0}（可用：{1}）".format(name, "、".join(categories)))
    raise SystemExit("分类歧义：{0} 匹配 {1}，请写更完整的名字".format(name, hits))


def resolve_packages(
    categories: Categories,
    sel: Iterable[str],
    category: Optional[str],
    file_path: Optional[str],
    all_: bool,
) -> List[str]:
    """按选择参数汇总目标库名。"""
    pkgs = list(sel)
    if category:
        pkgs.extend(categories[find_category(categories, category)])
    if file_path:
        from pathlib import Path

        txt = Path(file_path).read_text(encoding="utf-8", errors="replace")
        pkgs.extend(line.strip() for line in txt.splitlines()
                    if line.strip() and not line.strip().startswith("#"))
    if all_:
        pkgs.extend(all_packages(categories))
    if not pkgs:
        raise SystemExit("未指定任何库：请给库名 / --category / --file / --all")
    seen = set()
    return [p for p in pkgs if not (p in seen or seen.add(p))]


class CategoriesService(Service):
    """分类清单服务（插件行 ``categories``）。

    配置：``path``（清单路径，默认仓库根 ``requirements_categories.json``）。
    """

    name = "categories"

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        self.path = (self.config or {}).get("path") or default_categories_path()
        self.categories: Categories = load_categories(self.path)
        self._index: Dict[str, str] = {
            name: value for libs in self.categories.values() for name, value in libs.items()
        }

    # ── 统计 ─────────────────────────────────────────

    def curated_categories(self) -> List[str]:
        """精选分类（不含「🔍 自动发现」桶）。"""
        return [key for key in self.categories if key != AUTO_DISCOVERY_KEY]

    def auto_discovery(self) -> Dict[str, str]:
        return dict(self.categories.get(AUTO_DISCOVERY_KEY, {}))

    def stats(self) -> Dict[str, int]:
        total = sum(len(libs) for libs in self.categories.values())
        auto = len(self.auto_discovery())
        return {
            "categories": len(self.curated_categories()),
            "entries": total - auto,
            "auto_discovery": auto,
            "total_entries": total,
        }

    def audit(self) -> List[str]:
        return audit(self.categories)

    # ── 委托（保持与 CLI 相同语义）────────────────────

    def all_packages(self) -> List[str]:
        return all_packages(self.categories)

    def find_category(self, name: str) -> str:
        return find_category(self.categories, name)

    def target_of(self, name: str) -> str:
        return self._index.get(name, name)

    def pip_query_name(self, name: str, pkg: str) -> str:
        return pip_query_name(name, pkg)

    def resolve_packages(self, sel, category, file_path, all_) -> List[str]:
        return resolve_packages(self.categories, sel, category, file_path, all_)

    def __repr__(self) -> str:
        return "<CategoriesService path={0} stats={1}>".format(self.path, self.stats())
