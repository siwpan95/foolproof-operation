# -*- coding: utf-8 -*-
"""官方 PyPI 检查：ray/triton/ssh2-python/uvloop/pyaudio 是否有 win 3.14 可用轮子"""
import json
import urllib.request
import urllib.parse
from pip._vendor.packaging.utils import parse_wheel_filename
from pip._vendor.packaging import version as pv

NAMES = {
    "ray": "2.57.0",
    "triton": "3.7.1",
    "ssh2-python": "1.2.0.post1",
    "uvloop": "0.22.1",
    "pyaudio": "0.2.9",
}

def pypi_json(name):
    url = "https://pypi.org/pypi/" + urllib.parse.quote(name, safe="") + "/json"
    req = urllib.request.Request(url, headers={"User-Agent": "offi-check/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def rank(tags):
    best = 9
    for t in tags:
        plat, interp, abi = str(t.platform), str(t.interpreter), str(t.abi)
        if plat not in ("win_amd64", "win32", "win_arm64", "any"):
            continue
        if interp == "cp314":
            best = min(best, 0)
        elif abi == "abi3":
            best = min(best, 1)
        elif interp in ("py3", "py2.py3", "py2", "none"):
            best = min(best, 2)
    return best

for name, wanted in NAMES.items():
    data = pypi_json(name)
    releases = data.get("releases", {})
    win_usable = {}
    for v, files in releases.items():
        for f in files:
            fn = f.get("filename", "")
            if not fn.endswith(".whl"):
                continue
            try:
                n, ver, build, tags = parse_wheel_filename(fn)
            except Exception:
                continue
            r = rank(tags)
            if r < 9:
                win_usable.setdefault(r, []).append(str(ver))
    print(f"== {name} (目标 {wanted}) ==")
    if win_usable:
        for r in sorted(win_usable):
            vers = sorted(set(win_usable[r]), key=pv.parse, reverse=True)
            print(f"   rank{r}: {vers[:6]}{'...' if len(vers) > 6 else ''}")
        print(f"   结论: 可用，最高可用版本 -> rank{min(win_usable)}: {sorted(set(win_usable[min(win_usable)]), key=pv.parse, reverse=True)[0]}")
    else:
        print("   结论: 官方 PyPI 也无本机（win/3.14）可用轮子 → 跳过")
