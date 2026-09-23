# -*- coding: utf-8 -*-
"""清理 requirements_categories.json 的「🔍 自动发现」分类：
1) 去掉与其它分类重复的包；2) 去掉 PyPI 上不存在/标准库/垃圾名的包。"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
JSON_PATH = BASE / "foolproof_operation" / "requirements_categories.json"
FINAL = BASE / "retry_final_list.json"

def norm(s):
    return s.lower().strip().replace("-", "_")

with open(JSON_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

# 其它分类已知名
known_elsewhere = set()
for cat, libs in data.items():
    if "自动发现" in cat:
        continue
    for name in libs:
        known_elsewhere.add(norm(name))
        known_elsewhere.add(norm(libs[name] if isinstance(libs[name], str) else ""))

# 无效名来源：retry_final_list 的 skip_not_found / skip（垃圾行、标准库、本地模块）
bad = set()
if FINAL.exists():
    for e in json.loads(FINAL.read_text(encoding="utf-8")):
        if e.get("final_status") in ("skip_not_found", "skip"):
            bad.add(norm(e.get("name", "")))
# 附加手工黑名单
bad |= {"pip", "main", "git", "python", "urlparse", "tkmessagebox"}

auto_cat = None
for cat in data:
    if "自动发现" in cat:
        auto_cat = cat
        break
if auto_cat is None:
    print("未找到 自动发现 分类")
    sys.exit(0)

before = len(data[auto_cat])
removed = []
for name in list(data[auto_cat].keys()):
    val = data[auto_cat][name]
    n = norm(name)
    v = norm(val) if isinstance(val, str) else ""
    if n in known_elsewhere or v in known_elsewhere:
        removed.append((name, "与其它分类重复"))
        del data[auto_cat][name]
    elif n in bad or v in bad:
        removed.append((name, "PyPI 不存在/无效名"))
        del data[auto_cat][name]

with open(JSON_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

after = len(data[auto_cat])
print(f"清理完成：「{auto_cat}」{before} → {after}（移除 {len(removed)} 个）")
for name, why in removed:
    print(f"  - {name} ({why})")
