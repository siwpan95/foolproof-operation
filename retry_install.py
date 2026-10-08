# -*- coding: utf-8 -*-
"""按 retry_final_list.json 在**主解释器**（去符盘：/Python314）下批量安装，日志写入 重试安装日志.txt"""
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
PLAN = json.loads((BASE / "retry_final_list.json").read_text(encoding="utf-8"))
LOG = BASE / "重试安装日志.txt"

BIG = {"ray", "scikit-image", "arcade", "triton", "xgrammar", "thinc", "pyaudio", "newspaper3k"}

to_install = [e for e in PLAN if e.get("final_status") == "install"]
to_install.sort(key=lambda e: (1 if e["name"].lower().replace("_", "-") in BIG else 0,
                               e["name"].lower()))

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

log(f"===== 开始批量安装 {len(to_install)} 个包（Python: {sys.executable}）=====")
ok = fail = 0
for i, e in enumerate(to_install, 1):
    spec = e["install_spec"]
    log(f"[{i}/{len(to_install)}] ▶ 安装 {spec}（原请求: {e['spec']} | {e['reason']}）")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "--timeout", "60",
             "--retries", "3", "--disable-pip-version-check", spec],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        log(f"    ✖ {spec} 安装超时（30 分钟）")
        fail += 1
        continue
    if r.returncode == 0:
        log(f"    ✔ {spec} 安装成功")
        ok += 1
    else:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-5:])
        log(f"    ✖ {spec} 安装失败：\n{tail}")
        fail += 1
log(f"===== 批量安装结束：成功 {ok}，失败 {fail} =====")
