# -*- coding: utf-8 -*-
"""PyPI 校验 retry_candidates.json → 生成 retry_install_list.json 并更新报告"""
import json
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from pip._vendor.packaging import version as pv
from pip._vendor.packaging import specifiers as sp
from pip._vendor.packaging import requirements as pr

BASE = Path(__file__).parent
PY_VER = "3.14"
CAND = BASE / "retry_candidates.json"
OUT = BASE / "retry_install_list.json"
REPORT = BASE / "失败日志分析报告.md"

RENAME_SUGGEST = {
    "skimage": "scikit-image",
    "mysqldb": "mysqlclient",
    "opengl": "PyOpenGL",
    "win32gui": "pywin32",
    "win32process": "pywin32",
    "newspaper": "newspaper3k",
}
SKIP_REASON = {
    "urlparse": "Python 标准库，无需安装",
    "tkmessagebox": "Python 标准库（py2 遗留写法），无需安装",
    "flash_attn": "flash-attn 基本仅支持 Linux+CUDA，Windows 不建议尝试",
}

candidates = json.loads(CAND.read_text(encoding="utf-8"))

# 已安装包（F:\Python314）
installed = {}
try:
    r = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=json"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode == 0:
        for item in json.loads(r.stdout):
            installed[item["name"].lower()] = item.get("version", "?")
except Exception as e:
    print("获取已安装列表失败:", e)

print(f"F:\\Python314 已安装 {len(installed)} 个包")

def pypi_json(name, retries=2):
    url = "https://pypi.org/pypi/" + urllib.parse.quote(name, safe="") + "/json"
    req = urllib.request.Request(url, headers={"User-Agent": "req-retry/1.0"})
    last_err = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.5)
    return {"_error": last_err}

def compat_of(vstr, data):
    """返回 (py_ok, has_cp314, has_abi3, has_py3_any, file_count)"""
    files = data.get("releases", {}).get(vstr) or []
    py_ok = True
    for f in files:
        rp = f.get("requires_python")
        if rp:
            try:
                py_ok = sp.SpecifierSet(rp).contains(PY_VER)
            except Exception:
                py_ok = None
            break
    if py_ok is True and not any(f.get("requires_python") for f in files):
        rp = (data.get("info") or {}).get("requires_python")
        if rp:
            try:
                py_ok = sp.SpecifierSet(rp).contains(PY_VER)
            except Exception:
                py_ok = None
    names = [f.get("filename", "") for f in files]
    has_cp314 = any("cp314" in n for n in names)
    has_abi3 = any("abi3" in n for n in names)
    has_py3_any = any("py3-none-any" in n for n in names)
    return py_ok, has_cp314, has_abi3, has_py3_any, len(names)

def versions_of(data):
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

def pick(data, specifier):
    """选版本：优先满足 specifier 且有 cp314/abi3/py3-any wheel；否则放开 specifier"""
    vers = versions_of(data)
    if not vers:
        return None, "无发布版本"
    def usable(p_, v):
        py_ok, cp314, abi3, any3, cnt = compat_of(v, data)
        if py_ok is False:
            return False, "requires_python 不支持 3.14"
        return True, None
    # 1) 满足 specifier
    if specifier and str(specifier):
        for p, v in vers:
            try:
                if not specifier.contains(p, prereleases=False):
                    continue
            except Exception:
                continue
            ok, why = usable(p, v)
            if ok:
                py_ok, cp314, abi3, any3, cnt = compat_of(v, data)
                wheel = "cp314" if cp314 else ("abi3" if abi3 else ("py3-any" if any3 else "sdist-only"))
                return v, f"specifier 内匹配（wheel: {wheel}）"
    # 2) 放开 specifier 取最新可用
    for p, v in vers:
        ok, why = usable(p, v)
        if ok:
            py_ok, cp314, abi3, any3, cnt = compat_of(v, data)
            wheel = "cp314" if cp314 else ("abi3" if abi3 else ("py3-any" if any3 else "sdist-only"))
            note = f"最新可用（wheel: {wheel}）"
            if specifier and str(specifier):
                note += "，原版本约束无 3.14 可用版，已放开"
            return v, note
    return None, "所有版本 requires_python 均不支持 3.14"

results = []
for c in candidates:
    spec = c["spec"].strip()
    base = c["base"]
    count = c["count"]
    # 垃圾行
    if re.match(r"^(pip|pip3|python|py)(\.exe)?\s+", spec, re.I):
        results.append({"spec": spec, "name": base, "status": "skip",
                        "install_spec": None, "reason": "垃圾行：整行 shell 命令被当作包名"})
        print(f"skip 垃圾行: {spec}")
        continue
    # 标准库 / 本地模块
    if base in SKIP_REASON:
        results.append({"spec": spec, "name": base, "status": "skip",
                        "install_spec": None, "reason": SKIP_REASON[base]})
        print(f"skip {base}: {SKIP_REASON[base]}")
        continue
    # 解析 spec
    cleaned = re.split(r"\s+#", spec, maxsplit=1)[0].strip()
    try:
        req = pr.Requirement(cleaned)
        name = req.name
        specifier = req.specifier
        extras = req.extras
    except Exception:
        name = base
        specifier = None
        extras = set()
    name_key = name.lower().replace("_", "-")
    # PyPI 404 → 改名映射
    data = pypi_json(name_key)
    if data is None:
        rep = RENAME_SUGGEST.get(name_key)
        results.append({"spec": spec, "name": name_key, "status": "skip_not_found",
                        "install_spec": None,
                        "reason": ("PyPI 无此包。" + (f" 应改名为 {rep}。" if rep else " 疑似本地模块/拼写错误。"))})
        print(f"skip 404: {spec}" + (f" -> {rep}" if rep else ""))
        continue
    if isinstance(data, dict) and "_error" in data:
        results.append({"spec": spec, "name": name_key, "status": "network_error",
                        "install_spec": None, "reason": f"PyPI 查询失败: {data['_error']}"})
        print(f"network_error: {spec}: {data['_error']}")
        continue
    # 已安装？
    if name_key in installed:
        results.append({"spec": spec, "name": name_key, "status": "already_installed",
                        "install_spec": None,
                        "reason": f"F:\\Python314 已安装 {installed[name_key]}"})
        print(f"already: {spec} ({installed[name_key]})")
        continue
    v, why = pick(data, specifier)
    if v is None:
        results.append({"spec": spec, "name": name_key, "status": "skip_no_compatible",
                        "install_spec": None, "reason": why})
        print(f"skip no-compat: {spec}: {why}")
        continue
    extras_str = "[" + ",".join(sorted(extras)) + "]" if extras else ""
    keep = bool(specifier and str(specifier))
    if keep:
        # 若选中的版本仍满足原 specifier 就保留原规格，否则显式钉住选中版本
        try:
            if specifier.contains(pv.parse(v), prereleases=False):
                install_spec = cleaned
            else:
                install_spec = f"{name}{extras_str}=={v}"
        except Exception:
            install_spec = f"{name}{extras_str}=={v}"
    else:
        install_spec = f"{name}{extras_str}=={v}" if v else name
    results.append({"spec": spec, "name": name_key, "status": "install",
                    "install_spec": install_spec,
                    "reason": f"{why} → {install_spec}"})
    print(f"install: {spec} -> {install_spec} ({why})")

# 改名替代包（若未安装则加入安装列表）
renamed_targets = {}
for r_ in results:
    if r_["status"] == "skip_not_found" and r_["name"] in RENAME_SUGGEST:
        rep = RENAME_SUGGEST[r_["name"]]
        if rep not in renamed_targets:
            renamed_targets[rep] = r_["spec"]
for rep, orig in renamed_targets.items():
    key = rep.lower().replace("_", "-")
    if key in installed:
        results.append({"spec": orig, "name": key, "status": "already_installed",
                        "install_spec": None,
                        "reason": f"改名替代包 {rep} 已安装 {installed[key]}"})
        continue
    d2 = pypi_json(key)
    if d2 is None or (isinstance(d2, dict) and "_error" in d2):
        results.append({"spec": orig, "name": key, "status": "skip_not_found",
                        "install_spec": None, "reason": f"改名替代包 {rep} 亦不存在"})
        continue
    v, why = pick(d2, None)
    if v is None:
        results.append({"spec": orig, "name": key, "status": "skip_no_compatible",
                        "install_spec": None, "reason": f"改名替代包 {rep}: {why}"})
        continue
    results.append({"spec": orig, "name": key, "status": "rename_install",
                    "install_spec": f"{rep}=={v}",
                    "reason": f"原包名无效，安装替代包（{why}）"})
    print(f"rename_install: {orig} -> {rep}=={v}")

OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

from collections import Counter
st = Counter(r["status"] for r in results)
to_install = [r for r in results if r["status"] in ("install", "rename_install")]
print()
print("===== 校验汇总 =====")
for k, v in st.most_common():
    print(f"{k}: {v}")
print(f"待安装: {len(to_install)} 个")
print("写入 retry_install_list.json 完成")
