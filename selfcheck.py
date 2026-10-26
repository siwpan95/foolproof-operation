#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""foolproof-operation 无头自检（不创建 tkinter 窗口）。

只 import 包内纯逻辑模块（分类清单 / requirements 解析 / JSONL 任务日志 /
解释器解析 / 事件总线 / 插件清单），不 import gui.py、panels.py。验证五件事：

  1. 分类清单：24 个精选分类 + 自动发现桶结构合法，库名无重复；
  2. requirements.txt 解析：注释/选项行/误粘贴 shell 命令/行内注释/extras，
     以及 GBK 编码回退；
  3. 任务日志：JSONL 追加 → 读回 → 汇总（失败计数）→ 清空，坏行被跳过；
  4. 解释器解析：显式路径生效、不可用路径与本程序自身被拒；
  5. 插件清单：10 行全部可解析且插件目标可导入；非法清单被拒；
     事件总线的 emit / once / waterfall / off 语义。

用法：python selfcheck.py
退出码：0 成功 / 1 失败。
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 统一按 UTF-8 输出（分类名带 emoji，GBK 控制台直接 print 会 UnicodeEncodeError）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

from foolproof_operation.core.events import EventBus  # noqa: E402
from foolproof_operation.core.interpreter import (describe, is_self_executable,  # noqa: E402
                                                  resolve_python_exe)
from foolproof_operation.core.loader import (CompositionError, import_plugin,  # noqa: E402
                                             load_composition, parse_rows)
from foolproof_operation.core.tasklog import TaskLog, read_events, summarize  # noqa: E402
from foolproof_operation.plugins.categories import (AUTO_DISCOVERY_KEY, audit,  # noqa: E402
                                                    all_packages, find_category,
                                                    is_local_spec, load_categories,
                                                    pip_query_name, split_spec,
                                                    target_of)
from foolproof_operation.plugins.reqtxt import (parse_requirements_text,  # noqa: E402
                                               parse_requirements_txt)

COMPOSITION = os.path.join(ROOT, "foolproof_operation", "foolproof.composition.json")


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def expect(cond, msg):
    if not cond:
        fail(msg)


def check_categories():
    cats = load_categories()
    expect(audit(cats) == [], "分类清单结构非法：%r" % audit(cats))
    expect(AUTO_DISCOVERY_KEY in cats, "缺少「自动发现」桶：%r" % AUTO_DISCOVERY_KEY)
    curated = {k: v for k, v in cats.items() if k != AUTO_DISCOVERY_KEY}
    expect(len(curated) >= 20, "精选分类太少：%d" % len(curated))
    total_libs = sum(len(v) for v in curated.values())
    expect(total_libs >= 700, "精选库太少：%d" % total_libs)
    expect(all(v for v in curated.values()), "存在空分类")

    names = all_packages(cats)
    expect(len(names) == len(set(names)), "清单内库名有重复（all_packages 去重后变少）")
    expect(len(names) == sum(len(v) for v in cats.values()),
           "库名去重后数量与清单总数不符：%d vs %d" % (len(names), sum(len(v) for v in cats.values())))

    cat = find_category(cats, "加密")
    expect("加密" in cat, "find_category 子串匹配失败：%r" % cat)
    expect("Web" in find_category(cats, "Web"), "find_category('Web') 失败")
    try:
        find_category(cats, "不存在的分类")
    except SystemExit as e:
        expect("未知分类" in str(e), "未知分类的报错措辞不对：%r" % str(e))
    else:
        fail("未知分类未抛 SystemExit")

    expect(target_of(cats, "requests") == "requests", "target_of 未返回安装目标")
    expect(target_of(cats, "no-such-lib") == "no-such-lib", "target_of 对非清单项应原样返回")
    expect(pip_query_name("fastapi", "fastapi[all]") == "fastapi", "pip_query_name 未去掉 extras")
    expect(pip_query_name("bloodmusic", "G:/repos/bloodmusic") == "bloodmusic",
           "本地路径的 pip_query_name 应回落显示名")
    expect(is_local_spec("G:/repos/bloodmusic") and is_local_spec("-e G:/x"), "本地/选项 value 未识别")
    expect(not is_local_spec("requests"), "PyPI 包名被误判为本地路径")
    expect(split_spec("-e G:/repos/an-engine") == ["-e", "G:/repos/an-engine"], "split_spec 选项拆分不对")
    expect(split_spec("fastapi[all]") == ["fastapi[all]"], "split_spec 普通规格拆分不对")
    expect(split_spec("") == [], "split_spec 空输入应返回空列表")

    return len(curated), total_libs, len(cats[AUTO_DISCOVERY_KEY]), len(names)


def check_reqtxt():
    text = "\n".join([
        "# 注释行",
        "",
        "requests>=2.0",
        "fastapi[all]  # 行内注释",
        "-r other-requirements.txt",
        "--index-url https://pypi.example/simple",
        "pip install flask",
        "python -m pip install django",
        "numpy == 2.0",
        "   ",
    ])
    got = parse_requirements_text(text)
    want = [("requests", "requests>=2.0"), ("fastapi[all]", "fastapi[all]"), ("numpy", "numpy == 2.0")]
    expect(got == want, "requirements 文本解析不对：%r" % (got,))

    tmp = tempfile.mkdtemp(prefix="fop-selfcheck-")
    try:
        # GBK 编码文件：必须走 utf-8-sig → utf-8 → gbk 的回退链
        gbk_path = os.path.join(tmp, "gbk-requirements.txt")
        with open(gbk_path, "wb") as f:
            f.write("# 中文注释\nrequests>=2.0\nflask\n".encode("gbk"))
        got_gbk = parse_requirements_txt(gbk_path)
        expect(got_gbk == [("requests", "requests>=2.0"), ("flask", "flask")],
               "GBK requirements.txt 解析失败：%r" % (got_gbk,))

        missing = os.path.join(tmp, "nope.txt")
        expect(parse_requirements_txt(missing) == [], "不存在的 requirements.txt 应返回空列表")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return len(want)


def check_tasklog():
    tmp = tempfile.mkdtemp(prefix="fop-tasklog-")
    try:
        path = os.path.join(tmp, "sub", "tasks.jsonl")
        log = TaskLog(path)
        log.record("install", ok=True, package="requests", ms=12)
        log.record("install", ok=False, failed=1, package="bad-pkg", error="boom")
        log.record("scan", ok=True, count=3)
        log.record("envs", ok=False, failed=2)
        expect(os.path.isfile(path), "任务日志文件未创建")
        expect(log.written == 4, "TaskLog.written 计数不对：%d" % log.written)

        events = read_events(path)
        expect(len(events) == 4, "读回的日志条数不对：%d" % len(events))
        expect([e["event"] for e in events] == ["install", "install", "scan", "envs"],
               "日志事件顺序不对：%r" % [e["event"] for e in events])
        s = summarize(events)
        expect(s["total"] == 4 and s["failures"] == 3, "汇总失败计数不对：%r" % s)
        expect(s["events"] == {"install": 2, "scan": 1, "envs": 1}, "按事件名计数不对：%r" % s["events"])
        expect(s["latest"]["event"] == "envs", "latest 不是最后一条")
        expect(len(read_events(path, limit=2)) == 2, "limit 未生效")
        expect([e["event"] for e in read_events(path, limit=2)] == ["scan", "envs"],
               "limit 应取最后 N 条")

        with open(path, "a", encoding="utf-8") as f:
            f.write("{这不是 json}\n")
        expect(len(read_events(path)) == 4, "坏行未被跳过")

        log.clear()
        expect(read_events(path) == [], "clear 后仍能读到事件")

        disabled = TaskLog(os.path.join(tmp, "off.jsonl"), enabled=False)
        expect(disabled.record("install", ok=True) is None, "禁用状态下 record 应返回 None")
        expect(not os.path.exists(os.path.join(tmp, "off.jsonl")), "禁用状态下不应写文件")
        return 4
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_interpreter():
    resolved, source = resolve_python_exe(sys.executable)
    expect(resolved is not None and os.path.abspath(resolved) == os.path.abspath(sys.executable),
           "显式解释器未生效：%r（%s）" % (resolved, source))

    missing, why = resolve_python_exe(os.path.join(ROOT, "definitely-not-python.exe"))
    expect(missing is None and "不可用" in why, "不可用解释器未被拒：%r / %s" % (missing, why))

    fake, why2 = resolve_python_exe(argv=["--python", "requirements.exe"])
    expect(fake is None and "自身" in why2, "--python 指向本程序自身未被拒：%r / %s" % (fake, why2))

    expect(is_self_executable("requirements.exe"), "requirements.exe 未被识别为本程序自身")
    expect(not is_self_executable(sys.executable), "python.exe 被误判为本程序自身")
    expect("来源" in describe(sys.executable, "自检"), "describe 缺少来源说明")
    return 3


def check_plugin_manifest():
    rows = load_composition(COMPOSITION)
    expect(len(rows) >= 8, "插件清单行数太少：%d" % len(rows))
    ids = [r.id for r in rows]
    expect(len(ids) == len(set(ids)), "插件行 id 重复：%r" % ids)
    for row in rows:
        expect(":" in row.plugin, "插件目标缺少 模块:属性 形式：%s" % row.plugin)
        obj = import_plugin(row.plugin)
        expect(obj is not None, "插件目标无法导入：%s" % row.plugin)

    try:
        parse_rows([{"id": "a", "plugin": "m:f"}, {"id": "a", "plugin": "m:g"}])
    except CompositionError:
        pass
    else:
        fail("重复 id 的清单未被拒")
    try:
        parse_rows([{"id": "b"}])
    except CompositionError:
        pass
    else:
        fail("缺 plugin 的清单未被拒")

    bus = EventBus()
    calls = []
    bus.on("demo", lambda v: calls.append(("on", v)))
    bus.once("demo", lambda v: calls.append(("once", v)))
    expect(bus.emit("demo", 1) == 2 and bus.emit("demo", 2) == 1,
           "emit 返回的监听器数不对 / once 未自动摘除")
    expect(calls == [("on", 1), ("once", 1), ("on", 2)], "事件触发顺序不对：%r" % calls)
    bus.on("pipe", lambda v: v + 1)
    bus.on("pipe", lambda v: v * 2)
    expect(bus.waterfall("pipe", 3) == 8, "waterfall 中间件链结果不对")
    expect(bus.events() == ["demo", "pipe"], "已订阅事件清单不对：%r" % bus.events())
    listener = bus.listeners("demo")[0]
    expect(bus.off("demo", listener) is True, "退订已存在的监听器应返回 True")
    expect(bus.off("demo", listener) is False, "重复退订应返回 False")
    return len(rows)


def main():
    cats, libs, auto, names = check_categories()
    reqs = check_reqtxt()
    logged = check_tasklog()
    interp = check_interpreter()
    plugins = check_plugin_manifest()
    print("selfcheck ok: 分类清单 %d 个精选分类 + 自动发现桶 %d 条（共 %d 个库，无重复） / "
          "requirements 解析 %d 条（GBK 回退 + 跳过注释/选项/shell 命令） / "
          "JSONL 任务日志 %d 条往返（汇总失败数 + 清空 + 坏行跳过） / "
          "解释器解析 %d 种路径判定 / 插件清单 %d 行全部可导入 + 事件总线 emit/once/waterfall"
          % (cats, auto, names, reqs, logged, interp, plugins))


if __name__ == "__main__":
    main()
