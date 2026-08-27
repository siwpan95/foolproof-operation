# -*- coding: utf-8 -*-
"""最终重试：4 个镜像可装包 + 失败原因可见。日志追加到 重试安装日志.txt"""
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
LOG = BASE / "重试安装日志.txt"
SPECS = [
    ("outlines", "outlines==1.3.3"),
    ("py-backwards", "py-backwards==0.7"),
    ("py-backwards-astunparse", "py-backwards-astunparse==1.5.0.post1"),
    ("arcade", "arcade==3.3.3"),
]
SKIPPED = [
    ("ssh2-python", "无 Windows Python3.14 轮子（官方+镜像均无）"),
    ("uvloop", "无 Windows Python3.14 轮子（官方+镜像均无）"),
    ("ray", "无 Windows Python3.14 轮子（官方+镜像均无）"),
    ("triton", "无 Windows Python3.14 轮子（官方+镜像均无）"),
    ("pyaudio", "无 Windows Python3.14 轮子（官方+镜像均无）"),
]

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

log("===== 第二轮重试：4 个镜像可装包 =====")
for name, skip_why in SKIPPED:
    log(f"    ⊘ {name}：{skip_why}")

ok = fail = 0
for i, (name, spec) in enumerate(SPECS, 1):
    log(f"[{i}/{len(SPECS)}] ▶ 安装 {spec}")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "--timeout", "60",
             "--retries", "3", "--disable-pip-version-check", spec],
            capture_output=True, text=True, timeout=1200,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        log(f"    ✖ {spec} 安装超时（20 分钟）")
        fail += 1
        continue
    if r.returncode == 0:
        log(f"    ✔ {spec} 安装成功")
        ok += 1
    else:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-6:])
        log(f"    ✖ {spec} 安装失败：\n{tail}")
        fail += 1
log(f"===== 第二轮结束：成功 {ok}，失败 {fail} =====")
