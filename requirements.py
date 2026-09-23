import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import subprocess
import sys
import json
import os
import winsound
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# ========= 目标 Python 解释器 =========
# 默认使用运行本工具的 Python；可用命令行参数覆盖，例如：
#   python requirements.py --python F:\Python314\python.exe
PYTHON_EXE = sys.executable
if len(sys.argv) >= 3 and sys.argv[1] in ("--python", "-p"):
    _candidate = os.path.abspath(sys.argv[2])
    if Path(_candidate).exists():
        PYTHON_EXE = _candidate

# ========= 依赖库定义（从 JSON 加载，可按需增删） =========
def load_categories():
    """读取分类清单（实现见 plugins/categories.py：结构校验 fail-loud）。"""
    from plugins.categories import load_categories as _load

    return _load(str(Path(__file__).parent / "requirements_categories.json"))

CATEGORIES = load_categories()

#: pip 服务（懒建；实现见 plugins/pip.py）—— GUI 的 _pip_run 走它，行为与迁移前一致
_PIP_SERVICE = None


def _pip_service():
    global _PIP_SERVICE
    if _PIP_SERVICE is None:
        from plugins.pip import PipService

        _PIP_SERVICE = PipService(None, {"python_exe": PYTHON_EXE})
    return _PIP_SERVICE

# 自动发现时跳过的名字：标准库模块 / 常见误判
_STDLIB_OR_BOGUS_NAMES = {
    "urlparse", "urllib", "urllib2", "tkinter", "tkmessagebox", "tk", "sqlite3",
    "os", "sys", "subprocess", "re", "json", "datetime", "pathlib", "socket",
    "string", "typing", "winsound", "shutil", "glob", "argparse", "logging",
    "main", "git", "python",
}

def _pypi_exists(pkg_name, timeout=6):
    """向 PyPI 查询包是否存在（网络异常时返回 True 放行，不误伤）。"""
    url = "https://pypi.org/pypi/" + urllib.parse.quote(pkg_name, safe="") + "/json"
    req = urllib.request.Request(url, headers={"User-Agent": "requirements-gui/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return True

# ========= GUI =========
class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("📦 Python 依赖库安装器")
        self.geometry("800x620")
        self.vars = {}
        self.sound_enabled = tk.BooleanVar(value=True)
        self.fail_log_path = Path(__file__).parent / "失败日志.txt"
        self.pip_status = "checking"  # checking / ok / outdated / missing
        self.pip_version = "?"
        self.pip_latest = "?"

        self._build_ui()
        # 显示目标解释器，避免装错环境
        self.after(100, lambda: self.log_write(f"🐍 目标 Python：{PYTHON_EXE}\n\n（可用 python requirements.py --python <路径> 指定其它解释器）\n"))
        # 启动后自动检查 pip 状态
        self.after(300, lambda: threading.Thread(target=self._check_pip, daemon=True).start())

    def _build_ui(self):
        # ===== pip 状态栏 =====
        pip_frame = ttk.Frame(self, relief="ridge", borderwidth=1, padding=5)
        pip_frame.pack(fill="x", padx=10, pady=(8, 0))

        self.pip_icon_label = ttk.Label(pip_frame, text="⏳", font=("Arial", 14))
        self.pip_icon_label.pack(side="left", padx=(5, 0))

        self.pip_status_label = ttk.Label(
            pip_frame,
            text="正在检查 pip 状态…",
            font=("Microsoft YaHei", 10),
        )
        self.pip_status_label.pack(side="left", padx=8)

        self.pip_detail_label = ttk.Label(
            pip_frame,
            text="",
            font=("Microsoft YaHei", 9),
            foreground="gray",
        )
        self.pip_detail_label.pack(side="left", padx=5)

        self.pip_fix_btn = ttk.Button(
            pip_frame, text="🔧 修复 pip", command=self._fix_pip,
        )
        # 默认隐藏，问题出现时才显示

        # ===== 主布局 =====
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

        # 保存引用，供后续刷新使用
        self._checkbox_parent = scroll_frame
        self._build_categories_ui(scroll_frame)

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
        ttk.Button(btn_frame, text="卸载选中", command=self.start_uninstall).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="检查版本", command=self.start_check).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="一键更新", command=self.start_upgrade).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🔍 扫盘", command=self.start_scan).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="📄 读取 requirements.txt", command=self._scan_requirements_txt).pack(side="left", padx=5)

        # 音效开关
        ttk.Checkbutton(btn_frame, text="🔊 音效", variable=self.sound_enabled).pack(side="left", padx=10)

    # ========= 功能 =========
    def select_all(self):
        self._play_sound("click")
        for var, _ in self.vars.values():
            var.set(True)

    def deselect_all(self):
        self._play_sound("click")
        for var, _ in self.vars.values():
            var.set(False)

    def log_write(self, text):
        """线程安全日志：后台线程调用时自动调度回主线程执行。"""
        def _insert():
            self.log.insert("end", text + "\n")
            self.log.see("end")
        try:
            self.after(0, _insert)
        except RuntimeError:
            pass  # 窗口已销毁

    def _fail_log(self, text):
        """将失败信息追加写入 失败日志.txt"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.fail_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {text}\n")
        except Exception:
            pass

    # ========= pip 子进程封装 =========
    def _pip_run(self, cmd, timeout=900):
        """执行 pip 命令，返回 (returncode, stderr)。绝不抛异常。

        实现已迁移到 ``plugins/pip.py``（PipService.run）；本方法保留为 GUI 的稳定入口。
        """
        return _pip_service().run(cmd, timeout=timeout)

    @staticmethod
    def _stderr_tail(err, lines=6):
        from plugins.pip import stderr_tail
        return stderr_tail(err, lines)

    def _report_failure(self, name, action, pkg, stderr):
        """失败时把 pip 的真实报错尾部写入 GUI 日志和 失败日志.txt。"""
        tail = self._stderr_tail(stderr)
        msg = f"❌ {name} {action}失败"
        if tail:
            msg += f"：\n{tail}"
        self.log_write(msg + "\n")
        self._fail_log(msg)
        hint = self._failure_hint(stderr)
        if hint:
            self.log_write(f"💡 {hint}\n")
        self._play_sound("fail")

    @staticmethod
    def _failure_hint(stderr):
        from plugins.pip import failure_hint
        return failure_hint(stderr)

    # ========= 音效 =========
    def _play_sound(self, sound_type):
        """播放简单音效（winsound，零依赖）。在线程中执行避免阻塞 GUI。"""
        def _beep():
            if not self.sound_enabled.get():
                return
            try:
                if sound_type == "click":
                    winsound.Beep(800, 40)
                elif sound_type == "start":
                    winsound.Beep(523, 80)   # C5
                    winsound.Beep(659, 80)   # E5
                    winsound.Beep(784, 80)   # G5
                elif sound_type == "ok":
                    winsound.Beep(1047, 60)  # C6
                elif sound_type == "fail":
                    winsound.Beep(200, 150)
                elif sound_type == "warn":
                    winsound.Beep(400, 100)
                    winsound.Beep(350, 200)
                elif sound_type == "done":
                    winsound.Beep(523, 100)  # C5
                    winsound.Beep(659, 100)  # E5
                    winsound.Beep(784, 100)  # G5
                    winsound.Beep(1047, 150) # C6
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

    def start_install(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            self._play_sound("warn")
            messagebox.showwarning("提示", "请至少选择一个库")
            return

        self._play_sound("start")
        threading.Thread(target=self.install, args=(selected,), daemon=True).start()

    def install(self, packages):
        self.log_write("🚀 开始安装依赖库...\n")
        from cli import split_spec   # 共享：支持 "-e <路径>" 形式的 value

        for name, pkg in packages:
            self.log_write(f"📦 安装 {name}")
            rc, err = self._pip_run(
                [PYTHON_EXE, "-m", "pip", "install", "-U", *split_spec(pkg)]
            )
            if rc == 0:
                self.log_write(f"✅ {name} 安装成功\n")
                self._play_sound("ok")
            else:
                self._report_failure(name, "安装", pkg, err)

        self.log_write("🎉 安装完成！")
        self._play_sound("done")

    # ========= 检查版本 =========
    def start_check(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            self._play_sound("warn")
            messagebox.showwarning("提示", "请至少选择一个库")
            return
        self._play_sound("start")
        threading.Thread(target=self._check_versions, args=(selected,), daemon=True).start()

    def _check_versions(self, packages):
        self.log_write("🔍 正在检查版本...\n")

        # 一次性获取所有可更新的包（避免逐个查询太慢）
        outdated_map = {}
        try:
            result = subprocess.run(
                [PYTHON_EXE, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0:
                for item in json.loads(result.stdout):
                    outdated_map[item["name"].lower()] = {
                        "current": item.get("version", "?"),
                        "latest": item["latest_version"],
                    }
        except Exception:
            pass

        from cli import pip_query_name   # 共享：本地路径型 value 的包名解析
        for name, pkg in packages:
            try:
                # 去掉 extras（如 fastapi[all] -> fastapi）；本地路径型 value 回退显示名
                pkg_base = pip_query_name(name, pkg)
                result = subprocess.run(
                    [PYTHON_EXE, "-m", "pip", "show", pkg_base],
                    capture_output=True, text=True, timeout=30,
                    encoding="utf-8", errors="replace",
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
        self._play_sound("done")

    # ========= 一键更新 =========
    def start_upgrade(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            self._play_sound("warn")
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
        self._play_sound("start")
        threading.Thread(target=self._upgrade_versions, args=(selected,), daemon=True).start()

    def _upgrade_versions(self, packages):
        self.log_write("⬆️ 开始更新依赖库...\n")

        # pip 自身放最后更新
        sorted_pkgs = sorted(packages, key=lambda x: ("pip" in x[0].lower(), x[0]))

        from cli import split_spec   # 共享：支持 "-e <路径>" 形式的 value
        for name, pkg in sorted_pkgs:
            self.log_write(f"⬆️ 更新 {name}")
            cmd = [PYTHON_EXE, "-m", "pip", "install", "--upgrade", *split_spec(pkg)]
            if pkg.lower() == "pip":
                cmd = [PYTHON_EXE, "-m", "pip", "install", "--upgrade", "pip"]
            rc, err = self._pip_run(cmd)
            if rc == 0:
                self.log_write(f"✅ {name} 更新成功\n")
                self._play_sound("ok")
            else:
                self._report_failure(name, "更新", pkg, err)

        self.log_write("🎉 更新完成！")
        self._play_sound("done")

    # ========= 卸载 =========
    def start_uninstall(self):
        selected = [(n, p) for n, (v, p) in self.vars.items() if v.get()]
        if not selected:
            self._play_sound("warn")
            messagebox.showwarning("提示", "请至少选择一个库")
            return

        names = [n for n, _ in selected]
        ok = messagebox.askyesno(
            "确认卸载",
            f"即将卸载以下 {len(names)} 个库：\n\n" + "\n".join(f"  • {n}" for n in names) + "\n\n确定继续？",
        )
        if not ok:
            return

        self._play_sound("start")
        threading.Thread(target=self._uninstall, args=(selected,), daemon=True).start()

    def _uninstall(self, packages):
        self.log_write("🗑️ 开始卸载依赖库...\n")

        from cli import pip_query_name   # 共享：本地路径型 value 的包名解析
        for name, pkg in packages:
            self.log_write(f"🗑️ 卸载 {name}")
            pkg_base = pip_query_name(name, pkg)
            rc, err = self._pip_run(
                [PYTHON_EXE, "-m", "pip", "uninstall", "-y", pkg_base]
            )
            if rc == 0:
                self.log_write(f"✅ {name} 卸载成功\n")
                self._play_sound("ok")
            else:
                self._report_failure(name, "卸载", pkg, err)

        self.log_write("🎉 卸载完成！")
        self._play_sound("done")

    # ========= 扫盘 =========
    def start_scan(self):
        self._play_sound("start")
        threading.Thread(target=self._scan_disk, daemon=True).start()

    # 逐个环境跑 pip list 代价高（每个 1~3 秒），故对插件化发现出的候选设上限：
    # 上限只影响「本次逐个查包」的范围，不影响发现能力（完整清单见 `cli envs`）。
    SCAN_MAX_ENVS = 40

    def _discover_python_dirs(self):
        """环境发现：**优先插件化（USN 索引驱动），失败/无结果显式回退原目录启发式**。

        返回 ``(目录列表, 来源说明)``。插件化一条链是
        ``foolproof.composition.json``（fileindex → search → scanner）；
        索引未建、Everything 未跑、依赖缺失等任何情况都回退，绝不静默少报环境。
        """
        try:
            from core.runtime import build_runtime

            with build_runtime(strict=False) as runtime:
                scanner = runtime.ctx.get("scanner", None)
                if scanner is not None:
                    environments = scanner.discover(backend="auto")
                    result = scanner.last_result
                    if environments:
                        order = {"venv": 0, "conda": 1, "embed": 2, "standalone": 3, "legacy": 4}
                        environments.sort(
                            key=lambda env: (order.get(env.kind, 9), str(env.root).lower())
                        )
                        total = len(environments)
                        selected = environments[: self.SCAN_MAX_ENVS]
                        backend = result.backend if result is not None else "?"
                        note = f"插件化发现（backend={backend}，候选 {total} 个）"
                        if total > len(selected):
                            note += (
                                f"，本次逐个查包前 {len(selected)} 个"
                                f"（完整清单：python requirements.py cli envs）"
                            )
                        return [Path(env.root) for env in selected], note
        except Exception as exc:  # 插件化不可用不该让「扫盘」这个功能面消失
            self.log_write(f"⚠️ 插件化发现不可用，回退目录启发式：{exc}\n")
        return self._find_python_installations(), "目录启发式（原口径）"

    def _find_python_installations(self):
        """扫描系统中的 Python 安装位置"""
        found = set()

        # 0. 当前 Python
        found.add(Path(PYTHON_EXE).parent)

        # 1. 常见安装目录
        search_roots = [
            Path("C:/"),
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
        ]
        for root in search_roots:
            if not root.exists():
                continue
            try:
                for item in root.iterdir():
                    name = item.name.lower()
                    if item.is_dir() and ("python" in name or "anaconda" in name or "miniconda" in name):
                        found.add(item)
            except PermissionError:
                pass

        # 2. 用户目录下的 Python
        users_dir = Path("C:/Users")
        if users_dir.exists():
            try:
                for user_dir in users_dir.iterdir():
                    if not user_dir.is_dir():
                        continue
                    user_paths = [
                        user_dir / "AppData/Local/Programs/Python",
                        user_dir / "anaconda3",
                        user_dir / "miniconda3",
                        user_dir / "AppData/Local/Microsoft/WindowsApps",
                    ]
                    for up in user_paths:
                        if up.exists() and up.is_dir():
                            # 对于 Python 目录，递归找子目录
                            if "Python" in str(up):
                                try:
                                    for sub in up.iterdir():
                                        if sub.is_dir():
                                            found.add(sub)
                                except PermissionError:
                                    found.add(up)
                            else:
                                found.add(up)
            except PermissionError:
                pass

        # 3. 全局搜索 python.exe（限制深度以提速）
        for drive in ["D:/", "C:/", "F:/", "G:/", "E:/"]:
            drive_path = Path(drive)
            if not drive_path.exists():
                continue
            try:
                for item in drive_path.iterdir():
                    if not item.is_dir():
                        continue
                    name = item.name.lower()
                    if any(kw in name for kw in ["python", "anaconda", "miniconda", "venv", ".venv"]):
                        found.add(item)
                    # 也检查一级子目录
                    if item.name not in ("Windows", "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin", "System Volume Information"):
                        try:
                            for sub in item.iterdir():
                                if sub.is_dir():
                                    subname = sub.name.lower()
                                    if any(kw in subname for kw in ["python", "anaconda", "miniconda", "venv", ".venv"]):
                                        found.add(sub)
                        except PermissionError:
                            pass
            except PermissionError:
                pass

        return sorted(found, key=lambda p: str(p).lower())

    def _get_python_exe(self, path):
        """尝试在给定路径下找到 python.exe"""
        candidates = [
            path / "python.exe",
            path / "Scripts" / "python.exe",
            path / "bin" / "python.exe",
        ]
        for c in candidates:
            if c.exists():
                return c

        # 递归找 python.exe（只搜一层）
        try:
            for item in path.iterdir():
                if item.is_file() and item.name.lower() == "python.exe":
                    return item
                if item.is_dir():
                    py = item / "python.exe"
                    if py.exists():
                        return py
        except PermissionError:
            pass

        return None

    def _scan_disk(self):
        self.log_write("🔍 正在扫描系统中的 Python 安装...\n")

        python_dirs, source = self._discover_python_dirs()
        self.log_write(f"   发现来源：{source}\n")

        if not python_dirs:
            self.log_write("❌ 未找到任何 Python 安装。")
            self._play_sound("warn")
            return

        total_packages = 0
        all_found_packages = set()  # 收集所有扫到的包名
        for py_dir in python_dirs:
            py_exe = self._get_python_exe(py_dir)
            if py_exe is None:
                continue

            # 获取 Python 版本
            version = "?"
            try:
                r = subprocess.run(
                    [str(py_exe), "--version"],
                    capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace",
                )
                if r.returncode == 0:
                    version = r.stdout.strip()
            except Exception:
                pass

            self.log_write(f"\n{'='*60}")
            self.log_write(f"🐍 {py_dir}")
            self.log_write(f"   版本: {version}")

            # 获取已安装的包
            try:
                r = subprocess.run(
                    [str(py_exe), "-m", "pip", "list", "--format=columns"],
                    capture_output=True, text=True, timeout=30,
                    encoding="utf-8", errors="replace",
                )
                if r.returncode == 0:
                    lines = r.stdout.strip().split("\n")
                    pkg_count = len(lines) - 2  # 减去表头和分隔线
                    if pkg_count > 0:
                        total_packages += pkg_count
                        # 解析所有包名（lines[2:] 每行第一个空白分隔的 token）
                        for line in lines[2:]:
                            token = line.strip().split()[0] if line.strip() else ""
                            if token:
                                all_found_packages.add(token)
                        # 只显示前 30 个包，避免日志太长
                        max_show = 30
                        for line in lines[:max_show + 2]:
                            self.log_write(f"   {line}")
                        if pkg_count > max_show:
                            self.log_write(f"   ... 还有 {pkg_count - max_show} 个包（已省略）")
                    self.log_write(f"   📦 共 {pkg_count} 个已安装包")
                else:
                    self.log_write(f"   ⚠️ pip list 失败")
            except Exception as e:
                self.log_write(f"   ❌ 获取包列表失败：{e}")

        self.log_write(f"\n{'='*60}")
        self.log_write(f"\n✅ 扫盘完成！共找到 {len(python_dirs)} 个 Python 环境，{total_packages} 个已安装包。")

        # 自动将 JSON 中没有的包补充到分类中
        if all_found_packages:
            self._auto_add_to_categories(sorted(all_found_packages))

        self._play_sound("done")

    # ========= 自动补充 JSON 分类 =========
    def _auto_add_to_categories(self, package_names: list):
        """
        检查包名是否在 JSON 分类中，将缺失的包自动添加到「🔍 自动发现」分类。
        返回 added 列表，在 GUI 线程回调中刷新界面。
        """
        global CATEGORIES

        if not package_names:
            return

        json_path = Path(__file__).parent / "requirements_categories.json"

        # 收集所有已知包名（统一小写比较）
        known = set()
        for libs in CATEGORIES.values():
            for name in libs:
                known.add(name.lower())

        missing = []
        for pkg in package_names:
            pkg_lower = pkg.lower().strip()
            if not pkg_lower:
                continue
            # 尝试下划线和连字符两种形式
            if pkg_lower in known or pkg_lower.replace("-", "_") in known:
                continue
            if pkg_lower in _STDLIB_OR_BOGUS_NAMES:
                continue
            # PyPI 校验：不存在的包（本地模块/拼写错误）不再污染分类
            if not _pypi_exists(pkg_lower):
                self.log_write(f"   ⏭️ 跳过 {pkg_lower}（PyPI 上不存在，可能为本地模块或拼写错误）")
                continue
            missing.append(pkg_lower)

        if not missing:
            return

        # 读取-修改-写回 JSON
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        auto_category = "🔍 自动发现"
        if auto_category not in data:
            data[auto_category] = {}

        added = []
        for pkg in missing:
            # 使用原始大小写作为显示名，小写作为 pip 包名
            display_name = pkg
            if pkg not in data[auto_category] and display_name not in data[auto_category]:
                data[auto_category][display_name] = pkg
                added.append(display_name)

        if not added:
            return

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.log_write(f"⚠️ 更新 JSON 失败：{e}")
            return

        # 重新加载全局分类
        CATEGORIES = data

        # 记录日志
        self.log_write(f"\n📝 自动补充 {len(added)} 个新依赖库到「🔍 自动发现」分类：")
        for a in added:
            self.log_write(f"   + {a}")

        # 在主线程刷新 UI
        self.after(0, self._refresh_categories_ui)

    def _build_categories_ui(self, parent):
        """在给定父容器上构建分类复选框界面"""
        for widget in parent.winfo_children():
            widget.destroy()
        self.vars.clear()

        for cat, libs in CATEGORIES.items():
            ttk.Label(parent, text=cat, font=("Arial", 10, "bold")).pack(anchor="w", pady=(5, 0))
            for name, pkg in libs.items():
                # 默认不勾选，避免误点“开始安装”后批量安装 100+ 个库
                var = tk.BooleanVar(value=False)
                self.vars[name] = (var, pkg)
                cb = ttk.Checkbutton(parent, text=name, variable=var,
                                     command=lambda: self._play_sound("click"))
                cb.pack(anchor="w")
            ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=5)

    def _refresh_categories_ui(self):
        """重新加载 JSON 并刷新复选框界面（必须在主线程调用）"""
        self._build_categories_ui(self._checkbox_parent)

    # ========= requirements.txt 扫描与安装 =========
    @staticmethod
    def _parse_requirements_txt(filepath: str) -> list:
        """解析 requirements.txt → [(包名, 传给 pip 的安装规格), ...]。

        实现已迁移到 ``plugins/reqtxt.py``（解析规则逐字一致）；本方法保留为 GUI 的稳定入口。
        """
        from plugins.reqtxt import parse_requirements_txt
        return parse_requirements_txt(filepath)

    def _scan_requirements_txt(self):
        """扫描目录下的 requirements.txt 并显示结果"""
        self._play_sound("click")

        # 弹出目录选择对话框
        directory = filedialog.askdirectory(title="选择包含 requirements.txt 的目录")
        if not directory:
            return

        req_path = os.path.join(directory, "requirements.txt")
        if not os.path.exists(req_path):
            # 未检测到
            self._play_sound("warn")
            dialog = tk.Toplevel(self)
            dialog.title("📄 requirements.txt 扫描结果")
            dialog.geometry("450x200")
            dialog.resizable(False, False)
            dialog.transient(self)
            dialog.grab_set()

            main = ttk.Frame(dialog, padding=20)
            main.pack(fill="both", expand=True)

            ttk.Label(
                main, text="📄 requirements.txt",
                font=("Microsoft YaHei", 14, "bold"),
            ).pack(pady=(0, 10))

            ttk.Label(
                main,
                text=f"目录: {directory}",
                font=("Microsoft YaHei", 9),
                foreground="gray",
            ).pack()

            ttk.Label(
                main,
                text="未检测到 requirements.txt 文件",
                font=("Microsoft YaHei", 12),
                foreground="#e94560",
            ).pack(pady=15)

            ttk.Label(
                main,
                text="请确保该目录下存在 requirements.txt 文件",
                font=("Microsoft YaHei", 9),
                foreground="#888",
            ).pack()

            ttk.Button(main, text="关闭", command=dialog.destroy).pack(pady=(10, 0))
            return

        # 解析 requirements.txt
        packages = self._parse_requirements_txt(req_path)
        if not packages:
            self._play_sound("warn")
            messagebox.showinfo("提示", f"requirements.txt 存在但未解析到任何依赖包。\n文件路径: {req_path}")
            return

        self._play_sound("start")
        self.log_write(f"\n📄 检测到 requirements.txt: {req_path}")
        self.log_write(f"   共解析到 {len(packages)} 个依赖包\n")

        # 自动将 JSON 中没有的包补充到分类中
        pkg_names = [p[0] for p in packages]
        self._auto_add_to_categories(pkg_names)

        # 显示结果对话框
        dialog = tk.Toplevel(self)
        dialog.title(f"📄 requirements.txt — {len(packages)} 个依赖")
        dialog.geometry("600x480")
        dialog.transient(self)
        dialog.grab_set()

        main = ttk.Frame(dialog, padding=10)
        main.pack(fill="both", expand=True)

        # 头部信息
        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=f"📄 requirements.txt",
                  font=("Microsoft YaHei", 13, "bold")).pack(anchor="w")
        ttk.Label(header, text=req_path,
                  font=("Consolas", 8), foreground="gray").pack(anchor="w")

        # 可滚动的包列表（带复选框）
        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(tree_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮
        def _wheel(event):
            if not dialog.winfo_exists():
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        wheel_id = canvas.bind_all("<MouseWheel>", _wheel, add="+")

        def _cleanup():
            try:
                canvas.unbind_all("<MouseWheel>", wheel_id)
            except Exception:
                pass

        def _close_dialog():
            _cleanup()
            dialog.destroy()

        pkg_vars = {}
        for pkg_name, raw_line in packages:
            var = tk.BooleanVar(value=True)
            pkg_vars[pkg_name] = var
            row = ttk.Frame(scroll_frame)
            row.pack(fill="x", pady=1)
            ttk.Checkbutton(row, variable=var).pack(side="left")
            ttk.Label(row, text=pkg_name, font=("Consolas", 10, "bold"),
                      width=30, anchor="w").pack(side="left")
            ttk.Label(row, text=raw_line, font=("Consolas", 8),
                      foreground="gray").pack(side="left", padx=5)

        # 按钮区
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))

        ttk.Button(btn_frame, text="全选",
                   command=lambda: [v.set(True) for v in pkg_vars.values()]).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="清空",
                   command=lambda: [v.set(False) for v in pkg_vars.values()]).pack(side="left", padx=3)

        status_var = tk.StringVar(value=f"已选择 {len(packages)}/{len(packages)} 个")

        def _update_count(*args):
            selected = sum(1 for v in pkg_vars.values() if v.get())
            status_var.set(f"已选择 {selected}/{len(packages)} 个")

        # 绑定计数更新
        for var in pkg_vars.values():
            var.trace_add("write", _update_count)

        status_label = ttk.Label(btn_frame, textvariable=status_var, font=("Microsoft YaHei", 9))
        status_label.pack(side="left", padx=15)

        def _install_selected():
            to_install = [(n, raw) for n, raw in packages
                          if pkg_vars[n].get()]
            if not to_install:
                messagebox.showwarning("提示", "请至少选择一个包")
                return
            _close_dialog()
            self._play_sound("start")
            threading.Thread(target=self._install_from_requirements,
                             args=(to_install, req_path), daemon=True).start()

        ttk.Button(btn_frame, text="🚀 安装选中",
                   command=_install_selected).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="关闭",
                   command=_close_dialog).pack(side="right", padx=3)

        # 关闭时清理（winfo_exists 已确保 _wheel 函数安全）
        dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

    def _install_from_requirements(self, packages: list, source_path: str):
        """安装从 requirements.txt 解析的包"""
        self.log_write(f"\n🚀 从 requirements.txt 安装 {len(packages)} 个依赖包…\n")
        self.log_write(f"   源文件: {source_path}\n")

        success_count = 0
        fail_count = 0

        for pkg_name, raw_line in packages:
            self.log_write(f"📦 安装 {pkg_name} ({raw_line})")
            rc, err = self._pip_run(
                [PYTHON_EXE, "-m", "pip", "install", raw_line]
            )
            if rc == 0:
                self.log_write(f"✅ {pkg_name} 安装成功\n")
                self._play_sound("ok")
                success_count += 1
            else:
                self._report_failure(pkg_name, "安装", raw_line, err)
                fail_count += 1

        self.log_write(f"\n{'='*50}")
        self.log_write(f"📊 安装完成！成功 {success_count} 个，失败 {fail_count} 个")
        self._play_sound("done")

    # ========= pip 健康检查 =========
    def _check_pip(self):
        """启动时自动检查 pip 是否可用、版本、是否需要更新"""
        # 1. 检查 pip 是否可用
        try:
            r = subprocess.run(
                [PYTHON_EXE, "-m", "pip", "--version"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                self._set_pip_status("missing", "pip 未安装", "命令行找不到 pip 模块")
                return
            version_output = r.stdout.strip()
            # 格式: pip 24.0 from C:\... (python 3.13)
            current_ver = version_output.split()[1]
            self.pip_version = current_ver
        except Exception as e:
            self._set_pip_status("missing", "pip 检查失败", str(e))
            return

        # 2. 检查是否有更新
        try:
            r = subprocess.run(
                [PYTHON_EXE, "-m", "pip", "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if r.returncode == 0:
                for item in json.loads(r.stdout):
                    if item["name"].lower() == "pip":
                        self.pip_latest = item["latest_version"]
                        self.pip_version = item.get("version", self.pip_version)
                        self._set_pip_status(
                            "outdated",
                            f"pip 可更新",
                            f"当前 {self.pip_version}  →  最新 {self.pip_latest}",
                        )
                        return
        except Exception:
            pass

        # 3. 一切正常
        self._set_pip_status("ok", "pip 运行正常", f"版本 {self.pip_version}，已是最新")

    def _set_pip_status(self, status, main_text, detail_text):
        """更新 pip 状态栏显示（必须在主线程调用）"""
        def _update():
            self.pip_status = status
            self.pip_status_label.config(text=main_text)
            self.pip_detail_label.config(text=detail_text)

            if status == "ok":
                self.pip_icon_label.config(text="✅", foreground="green")
                self.pip_fix_btn.pack_forget()
            elif status == "outdated":
                self.pip_icon_label.config(text="⚠️", foreground="#e6a800")
                self.pip_fix_btn.pack(side="right", padx=5)
            elif status == "missing":
                self.pip_icon_label.config(text="❌", foreground="red")
                self.pip_fix_btn.pack(side="right", padx=5)
                self.log_write("⚠️ pip 未安装或不可用，请点击「🔧 修复 pip」按钮修复。")
            else:  # checking
                self.pip_icon_label.config(text="⏳", foreground="gray")

        self.after(0, _update)

    def _fix_pip(self):
        """弹出修复对话框，提供多种修复方案"""
        dialog = tk.Toplevel(self)
        dialog.title("🔧 修复 pip")
        dialog.geometry("550x400")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        # 居中
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 550) // 2
        y = self.winfo_y() + (self.winfo_height() - 400) // 2
        dialog.geometry(f"+{x}+{y}")

        main = ttk.Frame(dialog, padding=15)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="pip 修复工具",
            font=("Microsoft YaHei", 13, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            main,
            text="pip 是 Python 的包管理工具，安装/卸载/更新库都离不开它。\n"
                 "以下三种方案，任选其一即可：",
            font=("Microsoft YaHei", 9),
            foreground="#555",
            wraplength=500,
        ).pack(anchor="w", pady=(8, 12))

        # 方案一：直接升级
        f1 = ttk.LabelFrame(main, text="方案一：升级 pip（推荐）", padding=8)
        f1.pack(fill="x", pady=3)
        ttk.Label(
            f1,
            text="适用于 pip 已安装但版本过旧，或不确定是否安装的情况。",
            font=("Microsoft YaHei", 9),
        ).pack(anchor="w")
        ttk.Label(
            f1,
            text="等同于执行：python -m pip install --upgrade pip",
            font=("Consolas", 8),
            foreground="#888",
        ).pack(anchor="w", pady=(2, 5))
        ttk.Button(
            f1, text="⬆️ 执行升级",
            command=lambda: [dialog.destroy(), self._threaded_repair("upgrade")],
        ).pack(anchor="w")

        # 方案二：ensurepip
        f2 = ttk.LabelFrame(main, text="方案二：使用 ensurepip 安装", padding=8)
        f2.pack(fill="x", pady=3)
        ttk.Label(
            f2,
            text="Python 自带的引导安装方式，无需联网下载安装包。",
            font=("Microsoft YaHei", 9),
        ).pack(anchor="w")
        ttk.Label(
            f2,
            text="等同于执行：python -m ensurepip --upgrade",
            font=("Consolas", 8),
            foreground="#888",
        ).pack(anchor="w", pady=(2, 5))
        ttk.Button(
            f2, text="⬆️ 执行 ensurepip",
            command=lambda: [dialog.destroy(), self._threaded_repair("ensurepip")],
        ).pack(anchor="w")

        # 方案三：手动帮助
        f3 = ttk.LabelFrame(main, text="方案三：手动修复", padding=8)
        f3.pack(fill="x", pady=3)
        ttk.Label(
            f3,
            text="如果上述方案都失败了，可以尝试手动操作：",
            font=("Microsoft YaHei", 9),
        ).pack(anchor="w")
        steps = (
            "1. 打开命令行，输入：python -m pip install --upgrade pip\n"
            "2. 如果提示「不是内部或外部命令」，说明 Python 未加入环境变量\n"
            "   → 重新安装 Python 时勾选「Add Python to PATH」\n"
            "3. 如果提示权限不足，在命令前加：py -m pip install --upgrade pip"
        )
        ttk.Label(
            f3, text=steps,
            font=("Microsoft YaHei", 9),
            foreground="#333",
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(2, 5))
        ttk.Button(
            f3, text="重新检查 pip",
            command=lambda: [dialog.destroy(), self.after(300, lambda: threading.Thread(target=self._check_pip, daemon=True).start())],
        ).pack(anchor="w")

        ttk.Button(main, text="关闭", command=dialog.destroy).pack(pady=(10, 0))

    def _threaded_repair(self, method):
        """在后台线程中执行 pip 修复"""
        self._play_sound("start")
        self.log_write("🔧 开始修复 pip …\n")
        self._set_pip_status("checking", "正在修复 pip …", "请稍候")
        threading.Thread(target=self._do_repair, args=(method,), daemon=True).start()

    def _do_repair(self, method):
        if method == "upgrade":
            cmd = [PYTHON_EXE, "-m", "pip", "install", "--upgrade", "pip"]
        else:
            cmd = [PYTHON_EXE, "-m", "ensurepip", "--upgrade"]

        rc, err = self._pip_run(cmd, timeout=180)
        if rc == 0:
            self.log_write("✅ pip 修复成功！\n")
            self._play_sound("done")
        else:
            self.log_write(f"❌ 修复失败：\n{self._stderr_tail(err)}\n")
            self._play_sound("fail")

        # 修完后重新检查
        self._check_pip()


# ========= 启动 =========
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "cli":
        # 命令行模式：python requirements.py cli <子命令>（见 cli.py）
        from cli import run_cli
        sys.exit(run_cli(sys.argv[2:]))
    InstallerApp().mainloop()