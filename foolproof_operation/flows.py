"""界面无关的流程编排（阶段 B：把 GUI 里内联的安装/卸载/检查/更新/requirements 流程抽出来）。

**为什么抽**：这些流程原本内联在 ``gui.py`` 里（每个都直接拼 pip 命令 + 写日志 + 播声音），
导致「流程逻辑」与「tkinter 渲染」缠在一起、无法测试。抽出来之后：

- 流程只依赖**服务**（``installer`` / ``pip``）与一个**输出槽** ``sink``（log / sound / fail_log 三个回调）；
- GUI 的方法变成 3~5 行的薄适配器，CLI 或未来的 agent 也能复用同一套流程；
- 测试用假服务 + 记录型 sink 就能断言**逐条日志与音效序列**——这才是「行为零变化」可被验证的方式。

日志文本、音效序列、计数口径均与迁移前**逐字一致**（见 ``tests/test_flows.py`` 的断言）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .plugins.pip import failure_hint, stderr_tail

__all__ = [
    "Sink",
    "report_failure",
    "run_install",
    "run_upgrade",
    "run_uninstall",
    "run_check",
    "run_requirements",
]


@dataclass
class Sink:
    """流程的输出槽（GUI 传日志/音效回调；测试传记录器）。"""

    log: Callable[[str], None] = lambda _text: None
    sound: Callable[[str], None] = lambda _kind: None
    fail_log: Callable[[str], None] = lambda _text: None
    log_newline: str = "\n"          # GUI 的 log_write 自带换行；测试里可置 ""

    def write(self, text: str) -> None:
        self.log(text + self.log_newline)

    def play(self, kind: str) -> None:
        self.sound(kind)


def report_failure(sink: Sink, name: str, action: str, pkg: str, stderr: Optional[str]) -> str:
    """失败落账（迁移自 ``InstallerApp._report_failure``，文本逐字一致）。

    返回写出的消息正文，便于测试断言。
    """
    tail = stderr_tail(stderr)
    msg = "❌ {0} {1}失败".format(name, action)
    if tail:
        msg += "：\n{0}".format(tail)
    sink.write(msg + "\n")
    sink.fail_log(msg)
    hint = failure_hint(stderr)
    if hint:
        sink.write("💡 {0}\n".format(hint))
    sink.play("fail")
    return msg


def run_install(
    installer: Any,
    packages: Sequence[Tuple[str, str]],
    sink: Sink,
    *,
    splitted: Optional[Callable[[str], List[str]]] = None,
) -> Dict[str, int]:
    """逐个安装（``pip install -U <target>``）。返回 ``{"total", "ok", "failed"}``。"""
    from .cli import split_spec

    splitter = splitted or split_spec
    sink.write("🚀 开始安装依赖库...\n")
    counters = {"total": 0, "ok": 0, "failed": 0}
    for name, pkg in packages:
        sink.write("📦 安装 {0}".format(name))
        result = installer.install([name], targets=splitter(pkg), upgrade=True)
        counters["total"] += 1
        rc, err = _last(result)
        if rc == 0:
            sink.write("✅ {0} 安装成功\n".format(name))
            sink.play("ok")
            counters["ok"] += 1
        else:
            report_failure(sink, name, "安装", pkg, err)
            counters["failed"] += 1
    sink.write("🎉 安装完成！")
    sink.play("done")
    return counters


def run_upgrade(
    installer: Any,
    packages: Sequence[Tuple[str, str]],
    sink: Sink,
    *,
    splitted: Optional[Callable[[str], List[str]]] = None,
) -> Dict[str, int]:
    """一键更新（pip 自身放最后）。返回计数字典。"""
    from .cli import split_spec

    splitter = splitted or split_spec
    sink.write("⬆️ 开始更新依赖库...\n")
    sorted_pkgs = sorted(packages, key=lambda x: ("pip" in x[0].lower(), x[0]))
    counters = {"total": 0, "ok": 0, "failed": 0}
    for name, pkg in sorted_pkgs:
        sink.write("⬆️ 更新 {0}".format(name))
        targets = ["pip"] if pkg.lower() == "pip" else splitter(pkg)
        result = installer.install([name], targets=targets, upgrade=True)
        counters["total"] += 1
        rc, err = _last(result)
        if rc == 0:
            sink.write("✅ {0} 更新成功\n".format(name))
            sink.play("ok")
            counters["ok"] += 1
        else:
            report_failure(sink, name, "更新", pkg, err)
            counters["failed"] += 1
    sink.write("🎉 更新完成！")
    sink.play("done")
    return counters


def run_uninstall(
    installer: Any, packages: Sequence[Tuple[str, str]], sink: Sink
) -> Dict[str, int]:
    """逐个卸载（包名由 installer 用 ``pip_query_name`` 规范化）。"""
    sink.write("🗑️ 开始卸载依赖库...\n")
    counters = {"total": 0, "ok": 0, "failed": 0}
    for name, pkg in packages:
        sink.write("🗑️ 卸载 {0}".format(name))
        result = installer.uninstall([name])
        counters["total"] += 1
        rc, err = _last(result)
        if rc == 0:
            sink.write("✅ {0} 卸载成功\n".format(name))
            sink.play("ok")
            counters["ok"] += 1
        else:
            report_failure(sink, name, "卸载", pkg, err)
            counters["failed"] += 1
    sink.write("🎉 卸载完成！")
    sink.play("done")
    return counters


def run_check(
    installer: Any, packages: Sequence[Tuple[str, str]], sink: Sink
) -> Dict[str, int]:
    """版本检查（三条分支：未安装 / 可更新 / 已是最新；查询异常单独记「检查失败」）。"""
    sink.write("🔍 正在检查版本...\n")
    counters = {"total": 0, "missing": 0, "outdated": 0, "current": 0, "error": 0}
    results = installer.check_pairs(list(packages))
    for row in results:
        counters["total"] += 1
        if row.status == "error":
            sink.write("❌ {0} 检查失败：{1}".format(row.name, row.error))
            sink.fail_log("❌ {0} 检查失败：{1}".format(row.name, row.error))
            counters["error"] += 1
            continue
        if row.status == "missing":
            sink.write("⚠️  {0}: 未安装".format(row.name))
            counters["missing"] += 1
            continue
        if row.status == "outdated":
            sink.write("📦 {0}: {1}  →  {2} (可更新)".format(row.name, row.installed, row.latest))
            counters["outdated"] += 1
        else:
            sink.write("📦 {0}: {1} (已是最新)".format(row.name, row.installed))
            counters["current"] += 1
    sink.write("\n✅ 版本检查完成！")
    sink.play("done")
    return counters


def run_requirements(
    installer: Any,
    packages: Sequence[Tuple[str, str]],
    source_path: str,
    sink: Sink,
) -> Dict[str, int]:
    """按 requirements.txt 解析结果逐条安装（``pip install <原始规格>``）。"""
    sink.write("\n🚀 从 requirements.txt 安装 {0} 个依赖包…\n".format(len(packages)))
    sink.write("   源文件: {0}\n".format(source_path))
    counters = {"total": 0, "ok": 0, "failed": 0}
    for pkg_name, raw_line in packages:
        sink.write("📦 安装 {0} ({1})".format(pkg_name, raw_line))
        result = installer.install([pkg_name], targets=[raw_line])
        counters["total"] += 1
        rc, err = _last(result)
        if rc == 0:
            sink.write("✅ {0} 安装成功\n".format(pkg_name))
            sink.play("ok")
            counters["ok"] += 1
        else:
            report_failure(sink, pkg_name, "安装", raw_line, err)
            counters["failed"] += 1
    sink.write("\n{0}".format("=" * 50))
    sink.write("📊 安装完成！成功 {0} 个，失败 {1} 个".format(counters["ok"], counters["failed"]))
    sink.play("done")
    return counters


def _last(result: Any) -> Tuple[int, str]:
    """从 ``OperationResult`` 取最后一条 ``(rc, stderr)``（单库调用即该库的结果）。"""
    results = getattr(result, "results", None) or []
    if not results:
        return -1, "服务未返回结果"
    _name, rc, err = results[-1]
    return rc, err
