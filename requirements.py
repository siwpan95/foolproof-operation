import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import subprocess
import sys
import json
from pathlib import Path

# ========= 依赖库定义（从 JSON 加载，可按需增删） =========
def load_categories():
    json_path = Path(__file__).parent / "requirements_categories.json"
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

CATEGORIES = load_categories()

# ========= GUI =========
class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("📦 Python 依赖库安装器")
        self.geometry("720x520")
        self.vars = {}
        self.fail_log_path = Path(__file__).parent / "失败日志.txt"

        self._build_ui()

    def _build_ui(self):
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 左侧：可滚动的复选框区域
        left = ttk.Frame(frame, width=220)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        canvas = tk.Canvas(left, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮支持
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        for cat, libs in CATEGORIES.items():
            ttk.Label(scroll_frame, text=cat, font=("Arial", 10, "bold")).pack(anchor="w", pady=(5, 0))
            for name, pkg in libs.items():
                var = tk.BooleanVar(value=True)
                self.vars[name] = (var, pkg)
                ttk.Checkbutton(scroll_frame, text=name, variable=var).pack(anchor="w")
            ttk.Separator(scroll_frame, orient="horizontal").pack(fill="x", pady=5)

        # 右侧：日志
        right = ttk.Frame(frame)
        right.pack(side="right", fill="both", expand=True)

        self.log = scrolledtext.ScrolledText(right, height=22)
        self.log.pack(fill="both", expand=True)

        # 按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=5)

        ttk.Button(btn_frame, text="全选", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="清空", command=self.deselect_all).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="开始安装", command=self.start_install).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="检查版本", command=self.start_check).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="一键更新", command=self.start_upgrade).pack(side="left", padx=5)

    # ========= 功能 =========
    def select_all(self):
        for var, _ in self.vars.values():
            var.set(True)

    def deselect_all(self):
        for var, _ in self.vars.values():
            var.set(False)

    def log_write(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _fail_log(self, text):
        """将失败信息追加写入 失败日志.txt"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.fail_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {text}\n")
        except Exception:
            pass

    def start_install(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个库")
            return

        threading.Thread(target=self.install, args=(selected,), daemon=True).start()

    def install(self, packages):
        self.log_write("🚀 开始安装依赖库...\n")

        for name, pkg in packages:
            self.log_write(f"📦 安装 {name}")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-U", pkg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.log_write(f"✅ {name} 安装成功\n")
            except Exception as e:
                self.log_write(f"❌ {name} 安装失败：{e}\n")
                self._fail_log(f"❌ {name} 安装失败：{e}")

        self.log_write("🎉 安装完成！")

    # ========= 检查版本 =========
    def start_check(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个库")
            return
        threading.Thread(target=self._check_versions, args=(selected,), daemon=True).start()

    def _check_versions(self, packages):
        self.log_write("🔍 正在检查版本...\n")

        # 一次性获取所有可更新的包（避免逐个查询太慢）
        outdated_map = {}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                for item in json.loads(result.stdout):
                    outdated_map[item["name"].lower()] = {
                        "current": item.get("version", "?"),
                        "latest": item["latest_version"],
                    }
        except Exception:
            pass

        for name, pkg in packages:
            try:
                # 去掉 extras（如 fastapi[all] -> fastapi）
                pkg_base = pkg.split("[")[0]
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "show", pkg_base],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    self.log_write(f"⚠️  {name}: 未安装")
                    continue

                version = "?"
                for line in result.stdout.splitlines():
                    if line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                        break

                key = pkg_base.lower()
                if key in outdated_map:
                    self.log_write(
                        f"📦 {name}: {version}  →  {outdated_map[key]['latest']} (可更新)"
                    )
                else:
                    self.log_write(f"📦 {name}: {version} (已是最新)")
            except Exception as e:
                self.log_write(f"❌ {name} 检查失败：{e}")
                self._fail_log(f"❌ {name} 检查失败：{e}")

        self.log_write("\n✅ 版本检查完成！")

    # ========= 一键更新 =========
    def start_upgrade(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个库")
            return
        # pip 自身的更新需要特殊处理
        names = [n for n, _ in selected]
        if any("pip" in n.lower() for n in names):
            ok = messagebox.askyesno(
                "确认更新 pip",
                "你选择了更新 pip 自身。\n\n更新 pip 可能导致当前进程重启，是否继续？",
            )
            if not ok:
                return
        threading.Thread(target=self._upgrade_versions, args=(selected,), daemon=True).start()

    def _upgrade_versions(self, packages):
        self.log_write("⬆️ 开始更新依赖库...\n")

        # pip 自身放最后更新
        sorted_pkgs = sorted(packages, key=lambda x: ("pip" in x[0].lower(), x[0]))

        for name, pkg in sorted_pkgs:
            self.log_write(f"⬆️ 更新 {name}")
            try:
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
                if pkg.lower() == "pip":
                    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "pip"]
                subprocess.check_call(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.log_write(f"✅ {name} 更新成功\n")
            except Exception as e:
                self.log_write(f"❌ {name} 更新失败：{e}\n")
                self._fail_log(f"❌ {name} 更新失败：{e}")

        self.log_write("🎉 更新完成！")


# ========= 启动 =========
if __name__ == "__main__":
    InstallerApp().mainloop()