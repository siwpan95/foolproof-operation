# -*- coding: utf-8 -*-
"""GUI 冒烟测试：启动主窗口 + 打开两个面板，2.5 秒后自动关闭。

（实现已迁入 foolproof_operation 包；面板自阶段 C 起纳入冒烟范围。）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foolproof_operation.gui import InstallerApp

app = InstallerApp()
app.after(500, app._open_tasklog_panel)   # 📋 任务日志面板
app.after(1000, app._open_env_panel)      # 🐍 环境发现面板
app.after(1500, lambda: [w.destroy() for w in app.winfo_children() if w.winfo_class() == "Toplevel"])
app.after(2500, app.destroy)
app.mainloop()
print("GUI SMOKE TEST OK")