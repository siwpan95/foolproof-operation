# -*- coding: utf-8 -*-
"""按清华镜像库存重选失败包版本（Windows/3.14 感知）→ mirror_retry_list.json"""
import json
import re
import urllib.request
import urllib.parse
from pathlib import Path

from pip._vendor.packaging import version as pv
from pip._vendor.packaging.utils import parse_wheel_filename

BASE = Path(__file__).parent
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple/"
FAILED = [
    ("outlines", "0.1.11"),
    ("py-backwards", "0.7"),
    ("py-backwards-astunparse", "1.5.0.post3"),
    ("ssh2-python", "1.2.0.post1"),
    ("uvloop", "0.22.1"),
    ("arcade", "3.3.3"),
    ("pyaudio", "0.2.9"),
    ("ray", "2.57.0"),
    ("triton", "3.7.1"),
]

def mirror_files(name):
    url = MIRROR + urllib.parse.quote(name, safe="") + "/"
    req = urllib.request.Request(url, headers={"User-Agent": "mirror-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)
    return re.findall(r"<a[^>]*>([^<>]+)</a>", html), None

def wheel_rank(tags):
    """本机（Windows x64 + Python 3.14）可用性：0=cp314-win, 1=abi3-win, 2=通用, 9=不可用"""
    best = 9
    for t in tags:
        plat = str(t.platform)
        interp = str(t.interpreter)
        abi = str(t.abi)
        if plat not in ("win_amd64", "win32", "win_arm64", "any"):
            continue
        if interp == "cp314":
            best = min(best, 0)
        elif abi == "abi3":
            best = min(best, 1)
        elif interp in ("py3", "py2.py3", "py2", "none"):
            best = min(best, 2)
    return best

out = []
for name, wanted in FAILED:
    files, err = mirror_files(name)
    if err or not files:
        out.append({"name": name, "status": "mirror_error", "error": err or "无文件"})
        print(f"{name}: 镜像查询失败 {err}")
        continue
    wheels = []   # (ver, rank)
    sdists = []
    for fn in files:
        if fn.endswith(".whl"):
            try:
                n, ver, build, tags = parse_wheel_filename(fn)
                wheels.append((ver, wheel_rank(tags)))
            except Exception:
                continue
        elif fn.endswith((".tar.gz", ".zip")):
            sdists.append(fn)
    stables = [(v, r) for v, r in wheels if not v.is_prerelease and not v.is_devrelease and r < 9]
    candidates = stables or [(v, r) for v, r in wheels if r < 9]
    best = None
    for v, r in sorted(candidates, key=lambda t: (t[1], t[0]), reverse=True):
        # rank 最小优先，同 rank 版本最高
        if best is None or (r, v) < (best[1], best[0]):
            pass
    # 按 (rank asc, version desc) 排序取第一个（先按版本降序，再稳定按 rank 升序）
    ordered = sorted(candidates, key=lambda t: t[0], reverse=True)
    ordered = sorted(ordered, key=lambda t: t[1])
    chosen = ordered[0] if ordered else None
    if chosen:
        v, r = chosen
        out.append({"name": name, "wanted": wanted, "status": "ok",
                    "install_spec": f"{name}=={v}", "rank": r})
        print(f"{name}: {wanted} -> {name}=={v} (rank {r})")
        continue
    # 无可用轮子：纯 sdist 兜底
    latest_sdist = None
    for fn in sdists:
        m = re.match(re.escape(name) + r"-([0-9][^-]*)\.(tar\.gz|zip)$", fn)
        if m:
            try:
                v = pv.parse(m.group(1))
                if not v.is_prerelease and not v.is_devrelease:
                    if latest_sdist is None or v > latest_sdist:
                        latest_sdist = v
            except Exception:
                pass
    if latest_sdist is not None:
        out.append({"name": name, "wanted": wanted, "status": "sdist_only",
                    "install_spec": f"{name}=={latest_sdist}", "rank": 3})
        print(f"{name}: 无可用轮子，sdist 兜底 {latest_sdist}")
    else:
        out.append({"name": name, "wanted": wanted, "status": "none",
                    "install_spec": None, "rank": 9})
        print(f"{name}: 镜像上无本机可用文件（{len(wheels)} 轮子 / {len(sdists)} sdist）")

(BASE / "mirror_retry_list.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
