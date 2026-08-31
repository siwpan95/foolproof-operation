# -*- coding: utf-8 -*-
"""foolproof-operation 命令行接口（命令提示符直接使用）

单一入口保持 `python requirements.py`：
  - 无参数 / 未知参数 → 打开原有图形界面（向后兼容）
  - `python requirements.py cli <子命令> [选项]` → 命令行模式

子命令：
  list      [--category 分类名]            列出分类与库名
  install   <库名...|--category 名|--file req.txt|--all> [--python exe]
  uninstall <库名...|--category 名>        [--python exe]
  check     [库名...] [--category 名]      [--python exe]   版本与更新检查
  scan                                      扫描系统中的 Python 环境
  es        [ES 参数...]                    Everything 搜索（内置 es.exe，MIT © voidtools）

说明：
  - install/uninstall/check 的 pip 操作经 [--python 指定的解释器或本工具解释器] -m pip 执行；
  - `es` 依赖 Everything 主程序或服务在运行（es.exe 会连接本机 Everything 实例，
    未运行时给出明确提示）；完整语法见 tools/everything/README.md。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATEGORIES: dict = json.loads(
    (HERE / "requirements_categories.json").read_text(encoding="utf-8"))
ES_EXE = HERE / "tools" / "everything" / "es.exe"

# 全盘扫描时跳过的大目录（避免误入系统/备份区）
_SCAN_SKIP = {"Windows", "ProgramData", "$Recycle.Bin", "System Volume Information",
              "node_modules", ".git", "__pycache__"}


def _all_packages() -> list[str]:
    out = []
    for pkgs in CATEGORIES.values():
        out.extend(pkgs)
    # 去重保序
    seen = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def _find_category(name: str) -> str:
    """按精确键或唯一子串匹配分类（分类名带 emoji 前缀，允许用户省略）。"""
    if name in CATEGORIES:
        return name
    hits = [k for k in CATEGORIES if name in k]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"未知分类：{name}（可用：{'、'.join(CATEGORIES)}）")
    raise SystemExit(f"分类歧义：{name} 匹配 {hits}，请写更完整的名字")


def _resolve_packages(sel, category, file_path, all_) -> list[str]:
    """按选择参数汇总目标库名。"""
    pkgs = list(sel)
    if category:
        pkgs.extend(CATEGORIES[_find_category(category)])
    if file_path:
        txt = Path(file_path).read_text(encoding="utf-8", errors="replace")
        pkgs.extend(line.strip() for line in txt.splitlines()
                    if line.strip() and not line.strip().startswith("#"))
    if all_:
        pkgs.extend(_all_packages())
    if not pkgs:
        raise SystemExit("未指定任何库：请给库名 / --category / --file / --all")
    seen = set()
    return [p for p in pkgs if not (p in seen or seen.add(p))]


def _pip(args: list[str], python_exe: str | None) -> int:
    py = python_exe or sys.executable
    return subprocess.call([py, "-m", "pip", *args])


def cmd_list(args) -> int:
    cats = {_find_category(args.category): CATEGORIES[_find_category(args.category)]} \
        if args.category else CATEGORIES
    for name, pkgs in cats.items():
        print(f"[{name}]（{len(pkgs)}）")
        for p in pkgs:
            print("  -", p)
    return 0


def cmd_install(args) -> int:
    pkgs = _resolve_packages(args.packages, args.category, args.file, args.all)
    print(f"将安装 {len(pkgs)} 个库：{', '.join(pkgs[:8])}{'…' if len(pkgs) > 8 else ''}")
    cmd = ["install", "-U"] if args.upgrade else ["install"]
    return _pip(cmd + pkgs, args.python)


def cmd_uninstall(args) -> int:
    pkgs = _resolve_packages(args.packages, args.category, None, False)
    print(f"将卸载 {len(pkgs)} 个库：{', '.join(pkgs)}")
    return _pip(["uninstall", "-y", *pkgs], args.python)


def cmd_check(args) -> int:
    pkgs = _resolve_packages(args.packages, args.category, None, False) or _all_packages()
    py = args.python or sys.executable
    outdated = {}
    try:
        raw = subprocess.check_output(
            [py, "-m", "pip", "list", "--outdated", "--format=json"],
            text=True, errors="replace", timeout=120)
        for row in json.loads(raw):
            outdated[row["name"].lower()] = row
    except Exception:
        print("（pip list --outdated 失败，仅展示已安装版本）")
    for p in pkgs:
        try:
            info = subprocess.check_output(
                [py, "-m", "pip", "show", p], text=True, errors="replace", timeout=30)
            ver = next((ln.split(":", 1)[1].strip()
                        for ln in info.splitlines() if ln.startswith("Version:")), "?")
        except subprocess.CalledProcessError:
            print(f"{p}: 未安装")
            continue
        o = outdated.get(p.lower())
        if o:
            print(f"{p}: 已装 {ver} → 最新 {o.get('latest_version', '?')}（可更新）")
        else:
            print(f"{p}: 已装 {ver}（最新）")
    return 0


def cmd_scan(args) -> int:
    """扫描常见 Python 安装位置，列出解释器与包数。"""
    import os
    roots = []
    for base in ("C:/", "F:/", "D:/", "E:/"):
        roots.append(Path(base))
    found: dict[str, Path] = {}
    for base in roots:
        if not base.exists():
            continue
        for pat in ("Python*/python.exe", "Python*/*/python.exe",
                    "Users/*/AppData/Local/Programs/Python/*/python.exe"):
            for p in base.glob(pat):
                if not p.exists():
                    continue
                key = str(p).lower()
                if any(seg.lower() in _SCAN_SKIP for seg in p.parts):
                    continue
                found.setdefault(key, p)
    if not found:
        print("未发现 Python 环境（或在非常规路径）。")
        return 0
    print(f"发现 {len(found)} 个 Python 环境：")
    for i, p in enumerate(sorted(found.values()), 1):
        try:
            n = subprocess.check_output(
                [str(p), "-m", "pip", "list", "--format=freeze"],
                text=True, errors="replace", timeout=60).strip().count("\n") + 1
        except Exception:
            n = "?"
        print(f"{i}. {p}（已装包约 {n}）")
    return 0


def cmd_es(args) -> int:
    if not ES_EXE.exists():
        raise SystemExit(f"未找到内置 es.exe：{ES_EXE}")
    if not args.es_args:
        raise SystemExit("es 需要搜索词或参数。例：python requirements.py cli es -n 20 *.py")
    rc = subprocess.call([str(ES_EXE), *args.es_args])
    if rc == 8:
        print("\n[提示] Everything 未运行：请先启动 Everything.exe（或 Everything 服务），再执行 es。",
              file=sys.stderr)
    return rc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python requirements.py cli",
                                 description="Python 依赖库安装器 · 命令行接口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出分类与库名")
    p.add_argument("--category")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("install", help="安装库")
    p.add_argument("packages", nargs="*")
    p.add_argument("--category")
    p.add_argument("--file", help="requirements.txt 路径")
    p.add_argument("--all", action="store_true", help="安装全部分类的库")
    p.add_argument("--upgrade", action="store_true", help="等价 pip install -U")
    p.add_argument("--python", help="目标解释器（默认本工具解释器）")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("uninstall", help="卸载库")
    p.add_argument("packages", nargs="*")
    p.add_argument("--category")
    p.add_argument("--python")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("check", help="版本与更新检查")
    p.add_argument("packages", nargs="*")
    p.add_argument("--category")
    p.add_argument("--python")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("scan", help="扫描系统中的 Python 环境")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("es", help="Everything 搜索（内置 es.exe）")
    p.add_argument("es_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_es)
    return ap


def run_cli(argv: list[str]) -> int:
    # es 子命令的参数是 ES 原生旗标（-n/-s/-csv…），直接透传、不经 argparse
    if argv and argv[0] == "es":
        return cmd_es(argparse.Namespace(es_args=argv[1:]))
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
