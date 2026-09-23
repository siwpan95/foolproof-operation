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
  scan      [--backend walk|index|auto]    扫描系统中的 Python 环境（默认 walk=原口径）
  envs      [--backend auto|index|es|walk|legacy] [--build] [--deep] [--json f]
                                           环境发现（插件化：USN 索引驱动，覆盖深层 venv）
  es        [ES 参数...]                    Everything 搜索（内置 es.exe，MIT © voidtools）

说明：
  - install/uninstall/check 的 pip 操作经 [--python 指定的解释器或本工具解释器] -m pip 执行；
  - `es` 依赖 Everything 主程序或服务在运行（es.exe 会连接本机 Everything 实例，
    未运行时给出明确提示）；完整语法见 tools/everything/README.md；
  - `scan` 是历史口径（glob + 目录名启发式，会漏深层 venv），`envs` 走
    foolproof.composition.json 装配的插件（index → es → walk 依次回退，并打印是谁答的）。
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .plugins.categories import (
    all_packages as _all_packages_of,
    find_category as _find_category_of,
    is_local_spec as _is_local_spec,
    pip_query_name as _pip_query_name,
    resolve_packages as _resolve_packages_of,
    split_spec as _split_spec,
    target_of as _target_of,
    load_categories,
)
from .plugins.pip import PipService

HERE = Path(__file__).resolve().parent
# 分类清单经插件层加载（含结构校验 fail-loud）；CATEGORIES 保留为模块级名字供既有调用方使用
CATEGORIES: dict = load_categories(str(HERE / "requirements_categories.json"))
ES_EXE = HERE / "tools" / "everything" / "es.exe"

#: pip 服务实例（懒建，供 CLI 复用；GUI 与插件行各自持有自己的实例）
_PIP_SERVICE = None


def _pip_service() -> PipService:
    global _PIP_SERVICE
    if _PIP_SERVICE is None:
        _PIP_SERVICE = PipService(None, {})
    return _PIP_SERVICE


# ---- 本地 / 自研包支持 ----
# requirements_categories.json 的 value 通常是 PyPI 包名（可带 extras），
# 但「🏠 本地 / 自研包」里自研包的 value 是本地路径（pip install <path>）。
# 安装用 value；而 pip show / pip uninstall 要的是包名 —— 路径型 value 回退到显示名。
# 实现已迁移到 plugins/categories.py（此处保留同名包装，语义逐字一致）。
_LOCAL_SPEC = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|\.\.?[\\/]|-)")
_VALUE_INDEX: dict = {k: v for libs in CATEGORIES.values() for k, v in libs.items()}


def is_local_spec(pkg: str) -> bool:
    """value 是否为本地路径 / pip 选项形式（而非 PyPI 包名）。"""
    return _is_local_spec(pkg)


def target_of(name: str) -> str:
    """库名 → pip 安装目标（value）；非清单项原样返回。"""
    return _VALUE_INDEX.get(name, name)


def pip_query_name(name: str, pkg: str) -> str:
    """pip show / pip uninstall 需要的包名：去掉 extras；本地路径回落显示名。"""
    return _pip_query_name(name, pkg)


def split_spec(pkg: str) -> list[str]:
    """把分类 value 拆成 pip 参数列表，支持 `-e <路径>` 这类选项。"""
    return _split_spec(pkg)

# 全盘扫描时跳过的大目录（避免误入系统/备份区）
_SCAN_SKIP = {"Windows", "ProgramData", "$Recycle.Bin", "System Volume Information",
              "node_modules", ".git", "__pycache__"}


def _all_packages() -> list[str]:
    return _all_packages_of(CATEGORIES)


def _find_category(name: str) -> str:
    """按精确键或唯一子串匹配分类（分类名带 emoji 前缀，允许用户省略）。"""
    return _find_category_of(CATEGORIES, name)


def _resolve_packages(sel, category, file_path, all_) -> list[str]:
    """按选择参数汇总目标库名。"""
    return _resolve_packages_of(CATEGORIES, sel, category, file_path, all_)


def _pip(args: list[str], python_exe: str | None) -> int:
    """pip 调用（继承 stdio：pip 进度实时可见，语义与迁移前一致）。"""
    return _pip_service().call_live(args, python_exe=python_exe)


#: 任务日志（懒建；写文件、不打印，故不影响任何命令的输出）
_TASKLOG = None


def _tasklog():
    global _TASKLOG
    if _TASKLOG is None:
        from .core.tasklog import TaskLog

        _TASKLOG = TaskLog()
    return _TASKLOG


def cmd_tasks(args) -> int:
    """查看任务日志（JSONL 操作流水）：事件计数、失败计数与最近记录。"""
    from .core.tasklog import TaskLog

    log = TaskLog(getattr(args, "path", None) or None)
    if args.clear:
        log.clear()
        print(f"已清空任务日志：{log.path}")
        return 0
    events = log.events(limit=args.limit)
    summary = log.summary(limit=args.limit)
    if args.json:
        print(json.dumps(
            {"path": log.path, "summary": summary, "events": events},
            ensure_ascii=False, indent=2))
        return 0
    print(f"任务日志：{log.path}")
    if not events:
        print("（暂无记录）")
        return 0
    print(f"共 {summary['total']} 条（首 {summary['first']} / 末 {summary['last']}），"
          f"失败计数 {summary['failures']}")
    for name, count in sorted(summary["events"].items()):
        print(f"  {name}: {count}")
    print("最近记录：")
    for row in (events[-args.limit:] if args.limit else events[-10:]):
        extra = {k: v for k, v in row.items() if k not in ("ts", "event")}
        print(f"  {row.get('ts', '')}  {row.get('event', '')}  {extra}")
    return 0


def cmd_doctor(args) -> int:
    """启动审计：清单 / 分类 / 索引 / 检索后端 / 任务日志一次核完（只读）。"""
    from .core.audit import format_report
    from .core.runtime import build_runtime

    with build_runtime({"fileindex": {"volumes": getattr(args, "volumes", None) or None}}) as rt:
        audit = rt.ctx.get("audit")
        if audit is None:
            print("审计服务未装配（audit 行缺失）。", file=sys.stderr)
            return 1
        items = audit.run()
    if args.json:
        print(json.dumps([item.as_dict() for item in items], ensure_ascii=False, indent=2))
    else:
        print(format_report(items))
    return 1 if any(item.level == "error" for item in items) else 0


def cmd_list(args) -> int:
    cats = {_find_category(args.category): CATEGORIES[_find_category(args.category)]} \
        if args.category else CATEGORIES
    for name, pkgs in cats.items():
        print(f"[{name}]（{len(pkgs)}）")
        for p in pkgs:
            print("  -", p)
    return 0


def target_python(explicit: str | None = None) -> str:
    """解析目标 Python 解释器（**冻结版不能拿本程序自身当 Python**）。

    优先级与自指防护见 ``core/interpreter.py``；解析不到即 fail-loud——
    否则冻结版会把 `requirements.exe -m pip …` 当成 pip 调用，连锁拉起本程序自身（实测 22 个进程）。
    """
    from .core.interpreter import resolve_python_exe

    path, source = resolve_python_exe(explicit=explicit, argv=sys.argv[1:])
    if path is None:
        raise SystemExit("未找到可用的 Python 解释器：{0}".format(source))
    return path


def cmd_install(args) -> int:
    pkgs = _resolve_packages(args.packages, args.category, args.file, args.all)
    # 值可能是本地路径或带选项（-e ...），拆成 pip 参数
    targets = [a for p in pkgs for a in split_spec(target_of(p))]
    print(f"将安装 {len(pkgs)} 个库：{', '.join(targets[:8])}{'…' if len(targets) > 8 else ''}")
    cmd = ["install", "-U"] if args.upgrade else ["install"]
    py = target_python(args.python)
    log = _tasklog()
    log.record("install.start", count=len(pkgs), upgrade=bool(args.upgrade), python=py)
    rc = _pip(cmd + targets, py)
    log.record("install.done", count=len(pkgs), rc=rc, ok=(rc == 0))
    return rc


def cmd_uninstall(args) -> int:
    pkgs = _resolve_packages(args.packages, args.category, None, False)
    names = [pip_query_name(p, target_of(p)) for p in pkgs]
    print(f"将卸载 {len(names)} 个库：{', '.join(names)}")
    py = target_python(args.python)
    log = _tasklog()
    log.record("uninstall.start", count=len(names), python=py)
    rc = _pip(["uninstall", "-y", *names], py)
    log.record("uninstall.done", count=len(names), rc=rc, ok=(rc == 0))
    return rc


def cmd_check(args) -> int:
    """版本与更新检查（编排经 plugins/installer，打印格式与迁移前逐字一致）。"""
    from .core.context import Context
    from .plugins.categories import CategoriesService
    from .plugins.installer import InstallerService

    pkgs = _resolve_packages(args.packages, args.category, None, False) or _all_packages()
    py = target_python(args.python)
    ctx = Context()
    pip_service = ctx.provide("pip", PipService(None, {"python_exe": py}))
    ctx.provide("categories", CategoriesService(None, {"path": str(HERE / "requirements_categories.json")}))
    installer = InstallerService(ctx, {"python_exe": py})
    log = _tasklog()
    log.record("check.start", count=len(pkgs), python=py)
    results = installer.check(pkgs)
    if pip_service.last_outdated_error is not None:
        print("（pip list --outdated 失败，仅展示已安装版本）")
    for row in results:
        if row.installed is None:
            print(f"{row.name}: 未安装")
            continue
        if row.latest:
            print(f"{row.name}: 已装 {row.installed} → 最新 {row.latest}（可更新）")
        else:
            print(f"{row.name}: 已装 {row.installed}（最新）")
    log.record("check.done", count=len(pkgs),
               outdated=sum(1 for row in results if row.status == "outdated"),
               missing=sum(1 for row in results if row.status == "missing"),
               ok=pip_service.last_outdated_error is None)
    return 0


def cmd_scan(args) -> int:
    """扫描常见 Python 安装位置，列出解释器与包数。

    ``--backend walk``（默认）保持原有 glob 口径不变；``index`` / ``auto`` 则改走
    插件化环境发现（USN 索引优先、按需回退），二者的差异会在结果里显式标注。
    """
    backend = getattr(args, "backend", "walk") or "walk"
    if backend != "walk":
        return _scan_via_plugins(args, backend)
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


def _scan_via_plugins(args, backend: str) -> int:
    """走插件化环境发现（fileindex/search/scanner）列出环境。"""
    from .core.runtime import build_runtime

    overrides = {
        "fileindex": {"volumes": getattr(args, "volumes", None) or None},
        "search": {"walk_allow_whole_drives": bool(getattr(args, "allow_whole_drives", False))},
    }
    with build_runtime(overrides) as rt:
        scanner = rt.ctx.get("scanner")
        if scanner is None:
            print("环境发现服务未装配（scanner 行缺失）；已回退旧口径。", file=sys.stderr)
            args.backend = "walk"
            return cmd_scan(args)
        environments = scanner.discover(backend=backend, roots=getattr(args, "volumes", None) or None)
        result = scanner.last_result
    if result is not None:
        for name, outcome in result.attempts:
            print(f"[后端] {name}: {outcome}")
        print(f"[作答] {result.backend or '（无）'}")
    if not environments:
        print("未发现 Python 环境。")
        return 0
    print(f"发现 {len(environments)} 个 Python 环境（来源：插件化发现 / backend={backend}）：")
    for i, env in enumerate(environments, 1):
        kind = env.kind
        version = env.version or "?"
        exe = env.python_exe or "（未找到解释器）"
        print(f"{i}. [{kind}] {env.root}  版本={version}")
        print(f"   解释器: {exe}")
    return 0


def cmd_envs(args) -> int:
    """环境发现（插件化）：证据优先、可选深验证、可输出 JSON。

    与 ``scan`` 的区别：``scan`` 是历史口径（glob/目录名启发式），``envs`` 是索引驱动，
    覆盖深层 venv；默认 ``--backend auto``（索引 → Everything → 遍历），
    ``--build`` 会在索引缺失时先建索引（只读卷、不改卷状态）。
    """
    from .core.runtime import build_runtime

    volumes = args.volumes or None
    deep = bool(args.deep)
    status: list = []
    if args.build:
        with build_runtime({"fileindex": {"volumes": volumes}}) as rt:
            index = rt.ctx.get("fileindex")
            if index is None:
                print("索引服务未装配（fileindex 行缺失）。", file=sys.stderr)
                return 1
            for volume in index.detected_volumes():
                print(f"[建库] {volume} …", flush=True)
                state = index.build(volume, force=bool(args.force))
                status.append(state)
                records = state.get("records", 0)
                seconds = state.get("seconds", 0)
                print(f"  -> {state.get('status')} 记录={records} 耗时={seconds}s {state.get('detail', '')}")

    overrides = {
        "fileindex": {"volumes": volumes},
        "scanner": {"deep": deep},
        "search": {"walk_allow_whole_drives": bool(args.allow_whole_drives)},
    }
    with build_runtime(overrides) as rt:
        scanner = rt.ctx.get("scanner")
        if scanner is None:
            print("环境发现服务未装配（scanner 行缺失）。", file=sys.stderr)
            return 1
        environments = scanner.discover(
            backend=args.backend, roots=volumes, deep=deep
        )
        result = scanner.last_result
        # 注意：SearchResult 有空 __len__，空结果**为假**，故一律用 `is not None` 判存在，
        # 否则会把「索引不可用而回退」误报成「legacy（旧口径）」。
        print(f"[作答] {result.backend or '（无）'}" if result is not None else "[作答] legacy（旧口径）")
        if result is not None:
            for name, outcome in result.attempts:
                print(f"[后端] {name}: {outcome}")
    payload = {
        "count": len(environments),
        "backend": (result.backend if result is not None else "legacy"),
        "environments": [env.as_dict() for env in environments],
        "build": status,
    }
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON 已写入：{args.json}")
    print(f"共 {len(environments)} 个环境候选：")
    for i, env in enumerate(environments, 1):
        version = env.version or "?"
        valid = "" if env.valid is None else ("✓" if env.valid else "✗")
        print(f"{i:>4}. [{env.kind}{valid}] {env.root}  {version}")
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

    p = sub.add_parser("scan", help="扫描系统中的 Python 环境（默认保持原 glob 口径）")
    p.add_argument("--backend", choices=["walk", "index", "auto"], default="walk",
                   help="walk=原口径（默认）；index/auto=插件化发现（USN 索引优先）")
    p.add_argument("--volumes", nargs="*", help="限定卷（如 G: E:）")
    p.add_argument("--allow-whole-drives", action="store_true",
                   help="许可整盘遍历（默认拒绝：整卷遍历为分钟~小时级）")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("envs", help="环境发现（插件化：索引驱动，覆盖深层 venv）")
    p.add_argument("--backend", choices=["auto", "index", "es", "walk", "legacy"], default="auto")
    p.add_argument("--volumes", nargs="*", help="限定卷（如 G: E:）；walk 后端下应为具体目录")
    p.add_argument("--build", action="store_true", help="索引缺失时先建索引（只读卷）")
    p.add_argument("--force", action="store_true", help="配合 --build：强制重建")
    p.add_argument("--deep", action="store_true", help="对每个环境真跑 python --version 验证")
    p.add_argument("--allow-whole-drives", action="store_true",
                   help="许可整盘遍历（默认拒绝：整卷遍历为分钟~小时级）")
    p.add_argument("--json", help="结果落 JSON 文件")
    p.set_defaults(fn=cmd_envs)

    p = sub.add_parser("es", help="Everything 搜索（内置 es.exe）")
    p.add_argument("es_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_es)

    p = sub.add_parser("tasks", help="查看任务日志（JSONL 操作流水）")
    p.add_argument("--limit", type=int, default=None, help="只看最后 N 条")
    p.add_argument("--path", default=None, help="日志路径（默认在 LOCALAPPDATA 下的 foolproof-operation/logs）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--clear", action="store_true", help="清空日志后退出")
    p.set_defaults(fn=cmd_tasks)

    p = sub.add_parser("doctor", help="启动审计：清单/分类/索引/后端/日志（只读）")
    p.add_argument("--volumes", nargs="*", help="限定卷（如 G: E:）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)
    return ap


def _relax_output_encoding() -> None:
    """放宽 stdout/stderr 的错误策略，避免 GBK 控制台下 emoji 直接崩命令。

    实测（2026-09-23，默认代码页 936）：`cli list` 打印分类名里的 emoji 会抛
    ``UnicodeEncodeError: 'gbk' codec can't encode character '\\U0001f3ae'``，
    **源码版与 PyInstaller 冻结版都会中招**，且是既有缺陷（与本次重构无关）。
    这里只放宽**错误策略**、不改编码：中文照常显示，emoji 退化为 `?`。
    想完整显示 emoji 可先 `set PYTHONIOENCODING=utf-8`（README 已注明）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def run_cli(argv: list[str]) -> int:
    _relax_output_encoding()
    # es 子命令的参数是 ES 原生旗标（-n/-s/-csv…），直接透传、不经 argparse
    if argv and argv[0] == "es":
        return cmd_es(argparse.Namespace(es_args=argv[1:]))
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
