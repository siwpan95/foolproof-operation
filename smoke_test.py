# -*- coding: utf-8 -*-
"""GUI 冒烟测试：启动主窗口 2.5 秒后自动关闭"""
import importlib.util

spec = importlib.util.spec_from_file_location("requirements", "requirements.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

app = mod.InstallerApp()
app.after(2500, app.destroy)
app.mainloop()
print("GUI SMOKE TEST OK")
