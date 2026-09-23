"""pip 执行插件（行 ``pip``）：子进程调用 + 报错解读 + 健康检查与修复。

迁移来源（`requirements.py` / `cli.py`，**逻辑逐字保持一致**）：

- ``InstallerApp._pip_run`` → ``PipService.run``（绝不抛异常，超时与异常都转成 ``(rc, stderr)``）；
- ``InstallerApp._stderr_tail`` → ``stderr_tail``；
- ``InstallerApp._failure_hint`` → ``failure_hint``；
- ``InstallerApp._check_pip`` → ``PipService.health``（返回结构化状态，GUI 仍由自己渲染）；
- ``InstallerApp._do_repair`` → ``PipService.repair``（``upgrade`` / ``ensurepip`` 两条命令）。

**为什么迁移**：这些是「叶子逻辑」（纯函数 + 一次子进程调用），最值得集中且最容易被测试锁住；
GUI 里的**流程编排**（线程、日志、音效、进度）留在原处不动 —— 迁移与界面解耦，风险最小。
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.context import Service

__all__ = ["PipService", "stderr_tail", "failure_hint", "DEFAULT_TIMEOUT", "REPAIR_METHODS"]

#: 默认超时（秒）——沿用迁移前的 900。
DEFAULT_TIMEOUT = 900
#: 可用的 pip 修复方式（与 GUI「方案一/二」一致）。
REPAIR_METHODS = ("upgrade", "ensurepip")


def stderr_tail(err: Optional[str], lines: int = 6) -> str:
    """取 stderr 末尾若干行并缩进（迁移自 ``_stderr_tail``，逐字一致）。"""
    err = (err or "").strip()
    if not err:
        return ""
    tail = err.splitlines()[-lines:]
    return "\n".join("    {0}".format(t) for t in tail)


def failure_hint(stderr: Optional[str]) -> str:
    """把常见 pip 报错翻译成中文处置建议（迁移自 ``_failure_hint``，逐字一致）。"""
    err = (stderr or "").lower()
    if "no matching distribution" in err:
        return ("PyPI 上没有与当前 Python 版本匹配的发行包。常见原因：包名拼写错误、"
                "包已改名、或该版本号不支持当前 Python；可尝试去掉版本号改用最新版。")
    if "could not find a version that satisfies" in err:
        return "版本号不满足要求（可能太旧或不存在），建议去掉版本号重试。"
    if "metadata-generation-failed" in err or "building wheel" in err:
        return "该包需要本地编译且失败（通常需要 Visual Studio C++ 构建工具），可寻找预编译 wheel。"
    if "timed out" in err or "timeout" in err:
        return "网络超时，可更换 pip 镜像源（如清华源）后重试。"
    if "ssl" in err or "certificate" in err:
        return "网络证书/SSL 问题，可检查系统时间或更换镜像源。"
    return ""


class PipService(Service):
    """pip 服务（插件行 ``pip``）。

    配置：``python_exe``（目标解释器，默认本进程解释器）、``timeout``（默认 900 秒）。
    """

    name = "pip"

    def __init__(self, ctx: Any, config: Any = None) -> None:
        super().__init__(ctx, config)
        cfg: Dict[str, Any] = self.config or {}
        self.python_exe: str = cfg.get("python_exe") or sys.executable
        self.timeout: int = int(cfg.get("timeout") or DEFAULT_TIMEOUT)
        #: 上一次 ``outdated()`` 的失败原因（None = 成功）
        self.last_outdated_error: Optional[str] = None

    # ── 基础执行 ─────────────────────────────────────

    def run_full(
        self, cmd: Sequence[str], *, timeout: Optional[int] = None
    ) -> Tuple[int, str, str]:
        """执行命令，返回 ``(returncode, stdout, stderr)``。**绝不抛异常**。

        **自指防护**：若命令的首元素是本程序自身（冻结版把 ``sys.executable`` 当 Python 的经典陷阱），
        直接拒绝而不是 spawn —— 否则会重新拉起本程序（argv 不匹配时还会开 GUI 且永不退出，
        实测连锁 22 个进程并把 pip 检查挂死）。
        """
        from ..core.interpreter import is_self_executable

        argv = [str(x) for x in cmd]
        if not argv or not argv[0]:
            return -1, "", (
                "未解析到目标 Python 解释器：请用 --python <python.exe> 指定，"
                "或设置环境变量 FOOLPROOF_PYTHON。"
            )
        if is_self_executable(argv[0]):
            return -1, "", (
                "拒绝执行：目标解释器是本程序自身（{0}）。冻结版请用 --python <python.exe> 指定真实解释器，"
                "或设置环境变量 FOOLPROOF_PYTHON。".format(argv[0])
            )
        limit = self.timeout if timeout is None else int(timeout)
        try:
            r = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=limit,
                encoding="utf-8",
                errors="replace",
            )
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "命令超时（>{0} 秒）".format(limit)
        except Exception as e:
            return -1, "", str(e)

    def run(
        self, cmd: Sequence[str], *, timeout: Optional[int] = None
    ) -> Tuple[int, str]:
        """执行命令，返回 ``(returncode, stderr)``（迁移自 ``_pip_run``，语义一致）。"""
        rc, _out, err = self.run_full(cmd, timeout=timeout)
        return rc, err

    def pip(self, args: Sequence[str], *, python_exe: Optional[str] = None, timeout: Optional[int] = None):
        """``<python> -m pip <args>`` 的便捷包装。"""
        py = python_exe or self.python_exe
        return self.run([py, "-m", "pip", *args], timeout=timeout)

    def call_live(
        self, cmd: Sequence[str], *, python_exe: Optional[str] = None
    ) -> int:
        """执行命令并**继承当前进程的 stdio**（pip 的进度实时打印在终端上）。

        这是 ``cli._pip`` 迁移前的形态（``subprocess.call``）；不能改成捕获式，
        否则 `cli install` 会从「实时看到 pip 下载进度」变成「跑完一次性输出」——那是行为变化。
        """
        py = python_exe or self.python_exe
        return subprocess.call([py, "-m", "pip", *cmd])

    # ── 查询 ─────────────────────────────────────────

    def version(self, python_exe: Optional[str] = None) -> Tuple[Optional[str], str]:
        """``pip --version`` → ``(版本号或 None, 原始输出)``。"""
        py = python_exe or self.python_exe
        rc, out, err = self.run_full([py, "-m", "pip", "--version"], timeout=15)
        text = (out or "").strip()
        if rc != 0:
            return None, (text or err)
        parts = text.split()
        # 格式: pip 24.0 from C:\... (python 3.13)
        return (parts[1] if len(parts) > 1 else None), text

    def outdated(self, python_exe: Optional[str] = None, *, timeout: int = 120) -> Dict[str, dict]:
        """``pip list --outdated --format=json`` → ``{包名小写: 行}``。

        失败时返回空字典，并把原因记在 ``last_outdated_error``（调用方据此区分
        「没有可更新项」与「这次查询失败」，原始 CLI 就是按后者打印提示的）。
        **走 ``run_full`` 而非裸 subprocess**：否则绕过自指防护（冻结版会把本程序当 Python 拉起）。
        """
        py = python_exe or self.python_exe
        self.last_outdated_error = None
        rc, raw, err = self.run_full(
            [py, "-m", "pip", "list", "--outdated", "--format=json"], timeout=timeout
        )
        if rc != 0:
            self.last_outdated_error = (err or "").strip() or "rc={0}".format(rc)
            return {}
        try:
            return {row["name"].lower(): row for row in json.loads(raw or "[]")}
        except Exception as exc:
            self.last_outdated_error = str(exc)
            return {}

    def show(self, pkg: str, python_exe: Optional[str] = None) -> Dict[str, Any]:
        """``pip show <pkg>`` → ``{"state", "version", "detail"}``。

        ``state`` 区分三种情形 —— **「未安装」与「查询异常」必须分开**：
        GUI 的版本检查对前者记「⚠️ 未安装」、对后者记「❌ 检查失败」，
        合并成 ``None`` 会把两种日志混为一谈（迁移 GUI 流程时发现的行为差异）。
        """
        py = python_exe or self.python_exe
        rc, out, err = self.run_full([py, "-m", "pip", "show", pkg], timeout=30)
        if rc == -1:
            return {"state": "error", "version": None, "detail": (err or "").strip()}
        if rc != 0:
            return {"state": "missing", "version": None, "detail": (err or "").strip()}
        version = "?"
        for line in (out or "").splitlines():
            if line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
                break
        return {"state": "installed", "version": version, "detail": ""}

    def installed_version(self, pkg: str, python_exe: Optional[str] = None) -> Optional[str]:
        """``pip show <pkg>`` → ``Version:`` 值；未安装或查询失败返回 ``None``。"""
        return self.show(pkg, python_exe=python_exe)["version"]

    # ── 操作 ─────────────────────────────────────────

    def install(
        self,
        targets: Sequence[str],
        *,
        python_exe: Optional[str] = None,
        upgrade: bool = False,
        timeout: Optional[int] = None,
    ) -> Tuple[int, str]:
        args = ["install", "-U"] if upgrade else ["install"]
        return self.pip([*args, *targets], python_exe=python_exe, timeout=timeout)

    def uninstall(
        self, names: Sequence[str], *, python_exe: Optional[str] = None, timeout: Optional[int] = None
    ) -> Tuple[int, str]:
        return self.pip(["uninstall", "-y", *names], python_exe=python_exe, timeout=timeout)

    # ── 健康检查与修复 ───────────────────────────────

    def health(self, python_exe: Optional[str] = None) -> Dict[str, Any]:
        """pip 状态：``{"state", "main", "detail", "version", "latest"}``。

        与 ``_check_pip`` 同判据：不可用→missing；可更新→outdated；否则→ok。
        """
        py = python_exe or self.python_exe
        result: Dict[str, Any] = {
            "state": "ok",
            "main": "pip 运行正常",
            "detail": "",
            "version": None,
            "latest": None,
        }
        rc, out, err = self.run_full([py, "-m", "pip", "--version"], timeout=15)
        if rc == -1:
            # 含「自指拒绝」「解释器未解析」「超时」等硬错误：如实上报，不要含糊成「pip 未安装」
            result.update(state="missing", main="pip 检查失败", detail=(err or "").strip())
            return result
        if rc != 0:
            result.update(state="missing", main="pip 未安装", detail="命令行找不到 pip 模块")
            return result
        version_output = (out or "").strip()
        # 格式: pip 24.0 from C:\... (python 3.13)
        result["version"] = version_output.split()[1] if len(version_output.split()) > 1 else "?"

        rc2, raw, _err2 = self.run_full(
            [py, "-m", "pip", "list", "--outdated", "--format=json"], timeout=30
        )
        if rc2 == 0:
            try:
                for item in json.loads(raw or "[]"):
                    if item["name"].lower() == "pip":
                        result["latest"] = item["latest_version"]
                        result["version"] = item.get("version", result["version"])
                        result.update(
                            state="outdated",
                            main="pip 可更新",
                            detail="当前 {0}  →  最新 {1}".format(result["version"], result["latest"]),
                        )
                        return result
            except Exception:
                pass

        result["detail"] = "版本 {0}，已是最新".format(result["version"])
        return result

    def repair(self, method: str, python_exe: Optional[str] = None) -> Tuple[int, str]:
        """修复 pip（``upgrade`` / ``ensurepip``，迁移自 ``_do_repair``，超时 180 秒）。"""
        py = python_exe or self.python_exe
        if method == "upgrade":
            cmd = [py, "-m", "pip", "install", "--upgrade", "pip"]
        elif method == "ensurepip":
            cmd = [py, "-m", "ensurepip", "--upgrade"]
        else:
            raise ValueError(
                "未知修复方式：{0}（可用：{1}）".format(method, ", ".join(REPAIR_METHODS))
            )
        return self.run(cmd, timeout=180)

    def __repr__(self) -> str:
        return "<PipService python={0} timeout={1}>".format(self.python_exe, self.timeout)
