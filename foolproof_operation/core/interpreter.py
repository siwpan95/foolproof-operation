"""目标解释器解析：**冻结版（PyInstaller）必须另找真实 Python**。

背景（2026-09-23 实测事故）：冻结版里 ``sys.executable`` 指向 **本程序自身**（``requirements.exe``），
而 GUI/CLI 都拿它当 Python 用来执行 ``[PYTHON_EXE, "-m", "pip", ...]``。于是每个 pip 操作都变成
**重新启动本程序**：argv 不匹配 ``cli`` / ``--python`` 分支 → 又开一个 GUI 窗口且永不退出，
一个包一个窗口（实测连锁 22 个进程），pip 检查因此永久挂死、界面显示「pip 未安装或不可用」。

解析优先级：

1. 显式指定（``--python <exe>`` / 配置项）——**绝不接受本程序自身**；
2. 环境变量 ``FOOLPROOF_PYTHON``；
3. 非冻结运行：``sys.executable``（就是真 Python）；
4. 冻结运行：``PATH`` 上的 ``python`` / ``python3``，最后退到 Windows ``py`` 启动器；
5. 都找不到 → 返回 ``None`` 与原因，由调用方**显式提示**（不猜、不静默降级成本程序自身）。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional, Sequence, Tuple

__all__ = ["resolve_python_exe", "is_self_executable", "describe"]

#: 冻结版里本程序的可执行文件名（用于自指判断）
_SELF_NAMES = ("requirements.exe", "requirements-dbg.exe", "requirements", "requirements-dbg")


def is_self_executable(path: Optional[str]) -> bool:
    """``path`` 是否指本程序自身（冻结版的自指陷阱）。"""
    if not path:
        return False
    try:
        candidate = os.path.normcase(os.path.abspath(path))
    except Exception:
        return False
    if getattr(sys, "frozen", False):
        current = os.path.normcase(os.path.abspath(sys.executable or ""))
        if current and candidate == current:
            return True
    return os.path.basename(candidate).lower() in _SELF_NAMES


def _usable(path: Optional[str], *, allow_launcher: bool = False) -> bool:
    """路径可用性：存在 + 不是本程序自身（``py`` 启动器不要求是文件扩展名）。"""
    if not path or is_self_executable(path):
        return False
    if allow_launcher and os.path.basename(path).lower() in ("py", "py.exe"):
        return True
    return os.path.isfile(path)


def resolve_python_exe(
    explicit: Optional[str] = None,
    *,
    env: Optional[dict] = None,
    argv: Optional[Sequence[str]] = None,
) -> Tuple[Optional[str], str]:
    """解析目标解释器，返回 ``(路径或 None, 来源说明)``。

    ``argv`` 给定时会识别 ``--python <exe>`` / ``-p <exe>``（冻结版也能用）。
    """
    environ = os.environ if env is None else env

    if argv:
        args = list(argv)
        for flag in ("--python", "-p"):
            if flag in args:
                index = args.index(flag)
                if index + 1 < len(args):
                    candidate = args[index + 1]
                    if _usable(candidate):
                        return os.path.abspath(candidate), "命令行参数 {0}".format(flag)
                    if is_self_executable(candidate):
                        return None, "{0} 指定的是本程序自身，不能当 Python 解释器用".format(flag)
                    return None, "{0} 指定的解释器不可用：{1}".format(flag, candidate)

    if explicit:
        if _usable(explicit):
            return os.path.abspath(explicit), "配置指定"
        if is_self_executable(explicit):
            return None, "配置里的解释器是本程序自身，不能当 Python 解释器用"
        return None, "配置里的解释器不可用：{0}".format(explicit)

    override = environ.get("FOOLPROOF_PYTHON")
    if override:
        if _usable(override):
            return os.path.abspath(override), "环境变量 FOOLPROOF_PYTHON"
        return None, "环境变量 FOOLPROOF_PYTHON 指向的解释器不可用：{0}".format(override)

    if not getattr(sys, "frozen", False):
        current = sys.executable
        if _usable(current):
            return current, "当前解释器（源码运行）"
        return None, "当前解释器不可用：{0}".format(current)

    # 冻结版：sys.executable 是本程序自身，必须另找
    for name in ("python", "python3"):
        found = shutil.which(name)
        if _usable(found):
            return os.path.abspath(found), "PATH 上的 {0}".format(name)
    launcher = shutil.which("py")
    if launcher and not is_self_executable(launcher):
        return launcher, "Windows py 启动器（PATH）"
    return None, (
        "冻结版未能找到真实 Python 解释器：请用 --python <python.exe> 指定，"
        "或设置环境变量 FOOLPROOF_PYTHON（本程序自身不能当解释器用）"
    )


def describe(path: Optional[str], source: str) -> str:
    """一行诊断文本（供 ``cli doctor`` / GUI 状态栏显示）。"""
    if not path:
        return "未解析到解释器：{0}".format(source)
    return "{0}（来源：{1}）".format(path, source)
