# -*- coding: utf-8 -*-
"""终筛：按 PyPI 发布文件判断 3.14 可用性 + 特殊改名处理 → retry_final_list.json"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from pip._vendor.packaging import version as pv
from pip._vendor.packaging import specifiers as sp

BASE = Path(__file__).parent
PY_VER = "3.14"
SRC = BASE / "retry_install_list.json"
OUT = BASE / "retry_final_list.json"

entries = json.loads(SRC.read_text(encoding="utf-8"))

# 特殊处理（在通用逻辑之前）
SPECIAL = {
    "win32gui": ("skip", "pywin32 已安装（v312），win32gui 是其子模块，无需单独安装"),
    "pyqt5-sip": ("skip", "PyQt5 5.15.11 已安装并自带新版 PyQt5_sip，旧版 4.19.15 与现环境冲突"),
    "torch-npu": ("skip", "华为昇腾 NPU 专用包，无 Python 3.14 Windows 轮子，本机无需安装"),
}
RENAME = {
    "aip": "baidu-aip",
    "vlc": "python-vlc",
    "newspaper": "newspaper3k",
    "skimage": "scikit-image",
}

def pypi_json(name, retries=2):
    url = "https://pypi.org/pypi/" + urllib.parse.quote(name, safe="") + "/json"
    req = urllib.request.Request(url, headers={"User-Agent": "req-final/1.0"})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(1.5)
    return {"_error": "network"}

def py_ok_of(v, data):
    files = data.get("releases", {}).get(v) or []
    for f in files:
        rp = f.get("requires_python")
        if rp:
            try:
                return sp.SpecifierSet(rp).contains(PY_VER)
            except Exception:
                return None
    rp = (data.get("info") or {}).get("requires_python")
    if rp:
        try:
            return sp.SpecifierSet(rp).contains(PY_VER)
        except Exception:
            return None
    return True

def wheel_tags(v, data):
    files = data.get("releases", {}).get(v) or []
    t = {"cp314": False, "abi3": False, "pure": False, "win_none": False, "any_wheel": False}
    for f in files:
        n = f.get("filename", "")
        if not n.endswith(".whl"):
            continue
        t["any_wheel"] = True
        if "cp314" in n:
            t["cp314"] = True
        if "abi3" in n:
            t["abi3"] = True
        if "none-any" in n:
            t["pure"] = True
        if "none-" in n and "win_amd64" in n:
            t["win_none"] = True
    return t

def usable(v, data):
    if py_ok_of(v, data) is False:
        return False, "requires_python 不支持 3.14"
    t = wheel_tags(v, data)
    if t["cp314"] or t["abi3"]:
        return True, "有 cp314/abi3 轮子"
    if t["pure"] or t["win_none"]:
        return True, "有通用轮子"
    return False, "无 3.14 轮子（仅源码/其它平台轮子）"

def is_compiled_package(data):
    """最新版本是否带平台专属轮子（win/manylinux/macosx）→ 编译型包"""
    vs = sorted_versions(data)
    if not vs:
        return False
    for f in data.get("releases", {}).get(vs[0][1], []):
        n = f.get("filename", "")
        if n.endswith(".whl") and any(k in n for k in ("win", "manylinux", "macosx")):
            return True
    return False

# 已知可用的老式通用轮子（win_none 型，ctypes 实现，实测可跑 3.14）
UNIVERSAL_WHITELIST = {"pyaudio": "0.2.9"}

def sorted_versions(data):
    out = []
    for v in data.get("releases", {}):
        try:
            p = pv.parse(v)
        except Exception:
            continue
        if p.is_prerelease or p.is_devrelease:
            continue
        out.append((p, v))
    out.sort(key=lambda t: t[0], reverse=True)
    return out

def any_pure_exists(data):
    for _, v in sorted_versions(data):
        if wheel_tags(v, data)["pure"]:
            return True
    return False

final = []
for e in entries:
    if e["status"] not in ("install", "rename_install"):
        final.append({**e, "final_status": e["status"]})
        continue
    name = e["name"].lower().replace("_", "-")
    # 特殊处理
    if name in SPECIAL:
        st, why = SPECIAL[name]
        final.append({**e, "final_status": st, "install_spec": None, "reason": why})
        print(f"special: {name} -> {st}")
        continue
    if name in RENAME:
        new = RENAME[name]
        data = pypi_json(new)
        if data is None or "_error" in data:
            final.append({**e, "final_status": "skip_not_found",
                          "install_spec": None, "reason": f"改名包 {new} 亦不可用"})
            print(f"rename fail: {name} -> {new}")
            continue
        vs = sorted_versions(data)
        picked = None
        for p, v in vs:
            ok, _ = usable(v, data)
            if ok:
                picked = v
                break
        if picked is None:
            final.append({**e, "final_status": "skip_no_compatible",
                          "install_spec": None,
                          "reason": f"改名包 {new} 无 3.14 可用版本"})
            print(f"rename no-compat: {name} -> {new}")
            continue
        final.append({**e, "final_status": "install",
                      "install_spec": f"{new}=={picked}",
                      "reason": f"原包名无效，改安装 {new}=={picked}"})
        print(f"rename ok: {name} -> {new}=={picked}")
        continue
    # 通用逻辑：查所选版本的轮子；没有则按纯度规则换版本/跳过
    data = pypi_json(name)
    if data is None or "_error" in data:
        final.append({**e, "final_status": "network_error", "install_spec": None,
                      "reason": "PyPI 查询失败"})
        print(f"net fail: {name}")
        continue
    cur = e.get("install_spec") or ""
    m = re.search(r"==([^;]+)", cur)
    cur_v = m.group(1).strip() if m else None
    ok, why = usable(cur_v, data) if cur_v else (False, "无法解析当前版本")
    if ok:
        final.append({**e, "final_status": "install", "reason": why})
        print(f"keep: {name} {cur_v} ({why})")
        continue
    # 1) 白名单通用轮子
    if name in UNIVERSAL_WHITELIST:
        picked = UNIVERSAL_WHITELIST[name]
        final.append({**e, "final_status": "install",
                      "install_spec": f"{e['name']}=={picked}",
                      "reason": f"使用已知可用的通用轮子 {picked}"})
        print(f"whitelist: {name} -> {picked}")
        continue
    # 2) 编译型包：只接受 cp314/abi3 轮子
    if is_compiled_package(data):
        picked = None
        for p, v in sorted_versions(data):
            t = wheel_tags(v, data)
            if py_ok_of(v, data) is not False and (t["cp314"] or t["abi3"]):
                picked = v
                break
        if picked is not None:
            final.append({**e, "final_status": "install",
                          "install_spec": f"{e['name']}=={picked}",
                          "reason": f"原版本无 3.14 轮子，改用 {picked}（cp314/abi3 轮子）"})
            print(f"switch-compiled: {name} -> {picked}")
            continue
        final.append({**e, "final_status": "skip_no_wheel",
                      "install_spec": None,
                      "reason": "PyPI 上无 Python 3.14 Windows 预编译轮子，源码编译风险高，已跳过"})
        print(f"skip no-wheel: {name}")
        continue
    # 3) 纯 Python 包：直接装最新版（源码安装可行）
    latest = sorted_versions(data)[0][1]
    final.append({**e, "final_status": "install",
                  "install_spec": f"{e['name']}=={latest}",
                  "reason": f"纯 Python 包，改安装最新版 {latest}"})
    print(f"pure-latest: {name} -> {latest}")

OUT.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
from collections import Counter
st = Counter(x["final_status"] for x in final)
print()
print("===== 终筛汇总 =====")
for k, v in st.most_common():
    print(f"{k}: {v}")
to_do = [x for x in final if x["final_status"] == "install"]
print("最终待安装:", len(to_do))
for x in to_do:
    print("  -", x.get("install_spec") or x["spec"], "|", x["reason"])
