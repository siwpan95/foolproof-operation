# -*- coding: utf-8 -*-
"""GUI 冒烟测试：启动主窗口 2.5 秒后自动关闭（实现已迁入 foolproof_operation.gui）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foolproof_operation.gui import InstallerApp

app = InstallerApp()
app.after(2500, app.destroy)
app.mainloop()
print("GUI SMOKE TEST OK")
