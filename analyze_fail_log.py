# -*- coding: utf-8 -*-
"""解析 失败日志.txt → 生成 失败日志分析报告.md + retry_candidates.json"""
import re
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).parent
LOG = BASE / "失败日志.txt"
BT = chr(96)  # markdown 反引号

raw_lines = LOG.read_text(encoding="utf-8-sig").splitlines()
entries = []
for raw in raw_lines:
    raw = raw.strip()
    if not raw:
        continue
    ts = None
    m = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.*)$", raw)
    if m:
        ts, rest = m.group(1), m.group(2)
    else:
        rest = raw
    m2 = re.match(
        r"^❌\s+(.+?)\s+(安装|更新|卸载|检查)失败(?:（(.+?)）)?：Command\s+'(\[.*\])'\s+returned non-zero exit status 1\.$",
        rest,
    )
    if m2:
        pkg, action, venv, cmd_repr = m2.groups()
        try:
            cmd = ast.literal_eval(cmd_repr)
        except Exception:
            continue
        entries.append({
            "ts": ts, "pkg": pkg, "action": action, "venv": venv,
            "python": cmd[0] if cmd else None, "spec": cmd[-1] if cmd else rest,
        })
        continue
    m2b = re.match(r"^❌\s+(.+?)\s+(安装|更新|卸载|检查)失败：(.*)$", rest)
    if m2b:
        entries.append({
            "ts": ts, "pkg": m2b.group(1), "action": m2b.group(2),
            "venv": None, "python": None, "spec": m2b.group(3).strip(),
        })

def env_group(py):
    if not py:
        return "未知"
    p = py.replace("\\", "/").lower()
    if "f:/python314" in p:
        return r"F:\Python314（当前）"
    if "appdata/local/programs/python" in p or "pythoncore" in p:
        return "旧 Python（C 盘 AppData）"
    if ".venv" in p or "/venv/" in p:
        return "项目 venv（G 盘等）"
    return "其它"

env_counter = Counter(env_group(e["python"]) for e in entries)
spec_map = defaultdict(list)
for e in entries:
    spec_map[e["spec"]].append(e)

RENAME_SUGGEST = {
    "skimage": ("scikit-image", "PyPI 包名为 scikit-image，skimage 只是导入名"),
    "mysqldb": ("mysqlclient", "PyPI 包名为 mysqlclient（MySQL-python 已停止维护）"),
    "opengl": ("PyOpenGL", "PyPI 包名为 PyOpenGL"),
    "win32gui": ("pywin32", "win32gui 是 pywin32 的子模块，不在 PyPI 单独发布"),
    "win32process": ("pywin32", "win32process 是 pywin32 的子模块，不在 PyPI 单独发布"),
    "tkmessagebox": (None, "Python2 的 tkinter 子模块，Python3 内置，无需安装"),
    "urlparse": (None, "Python 标准库模块，无需安装"),
    "newspaper": ("newspaper3k", "PyPI 包名为 newspaper3k（newspaper 已废弃）"),
}
STDLIB_OR_LOCAL = {
    "urlparse": "Python 标准库",
    "tkmessagebox": "Python 标准库（py2 遗留写法）",
    "main": "本地模块名（非 PyPI 包）",
    "git": "非 PyPI 包（Git 本身是系统工具）",
    "behavior_tree": "本地模块名（非 PyPI 包）",
    "backrooms_raycaster": "本地模块名（非 PyPI 包）",
    "compile_decompile": "本地模块名（非 PyPI 包）",
    "dynamic_loss_scaler": "本地模块名（非 PyPI 包）",
    "sampler_system": "本地模块名（非 PyPI 包）",
    "separate_back": "本地模块名（非 PyPI 包）",
    "vocal_coupling": "本地模块名（非 PyPI 包）",
    "vst3": "本地模块名（非 PyPI 包）",
    "cublas_ops": "本地模块名（非 PyPI 包）",
    "test_probe": "本地模块名（非 PyPI 包）",
    "flash_attn": "flash-attn 基本仅支持 Linux+CUDA，Windows 上不建议尝试",
}

rows = []
for spec, es in sorted(spec_map.items(), key=lambda kv: -len(kv[1])):
    s = spec.strip()
    note = ""
    if re.match(r"^(pip|pip3|python|py)(\.exe)?\s+", s, re.I):
        note = "垃圾行：把 'pip install xxx' 整行当成了包名"
    base = re.split(r"[=<>!~;[]", s, maxsplit=1)[0].strip().lower()
    if not note and base in STDLIB_OR_LOCAL:
        note = STDLIB_OR_LOCAL[base]
    if not note and base in RENAME_SUGGEST:
        rep, why = RENAME_SUGGEST[base]
        note = "改名：" + (rep + "（" + why + "）" if rep else why)
    pinned = bool(re.search(r"(==|>=|<=|!=|~=|<|>)", s))
    rows.append({
        "spec": spec,
        "count": len(es),
        "actions": sorted(set(e["action"] for e in es)),
        "first": min(e["ts"] for e in es if e["ts"]) or "?",
        "last": max(e["ts"] for e in es if e["ts"]) or "?",
        "envs": sorted(set(env_group(e["python"]) for e in es)),
        "note": note,
        "pinned": pinned,
        "base": base,
    })

lines = []
lines.append("# 失败日志分析报告")
lines.append("")
lines.append("- 日志文件：" + BT + "失败日志.txt" + BT)
lines.append(f"- 失败记录总数：**{len(entries)}** 条")
lines.append(f"- 唯一安装规格：**{len(rows)}** 个")
lines.append("")
lines.append("## 1. 按 Python 环境统计")
lines.append("")
lines.append("| 环境 | 失败条数 |")
lines.append("|---|---|")
for env, cnt in env_counter.most_common():
    lines.append(f"| {env} | {cnt} |")
lines.append("")
lines.append("## 2. 明确无效的规格（垃圾行 / 标准库 / 本地模块）")
lines.append("")
lines.append("| 规格 | 次数 | 原因 |")
lines.append("|---|---|---|")
for r in rows:
    if r["note"] and ("垃圾" in r["note"] or "标准库" in r["note"] or "本地模块" in r["note"] or "Linux" in r["note"]):
        lines.append("| " + BT + r['spec'] + BT + " | " + str(r['count']) + " | " + r['note'] + " |")
lines.append("")
lines.append("## 3. 疑似改名（应按新包名安装）")
lines.append("")
lines.append("| 规格 | 次数 | 说明 |")
lines.append("|---|---|---|")
for r in rows:
    if r["note"] and r["note"].startswith("改名"):
        lines.append("| " + BT + r['spec'] + BT + " | " + str(r['count']) + " | " + r['note'] + " |")
lines.append("")
lines.append("## 4. 钉死旧版本（多数不支持 Python 3.14，待 PyPI 校验）")
lines.append("")
lines.append("| 规格 | 次数 | 首次失败 | 最后失败 |")
lines.append("|---|---|---|---|")
for r in rows:
    if r["pinned"] and not r["note"]:
        lines.append("| " + BT + r['spec'] + BT + " | " + str(r['count']) + " | " + r['first'] + " | " + r['last'] + " |")
lines.append("")
lines.append("## 5. 未加版本号的真实包（待 PyPI 校验后重试）")
lines.append("")
lines.append("| 规格 | 次数 | 动作 | 最后失败 |")
lines.append("|---|---|---|---|")
for r in rows:
    if not r["pinned"] and not r["note"]:
        lines.append("| " + BT + r['spec'] + BT + " | " + str(r['count']) + " | " + '/'.join(r['actions']) + " | " + r['last'] + " |")
lines.append("")

(BASE / "失败日志分析报告.md").write_text("\n".join(lines), encoding="utf-8")

(BASE / "retry_candidates.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
)

print(f"解析完成：{len(entries)} 条失败记录，{len(rows)} 个唯一规格")
print(f"环境分布：{dict(env_counter)}")
print("报告已写入 失败日志分析报告.md；候选清单已写入 retry_candidates.json")
