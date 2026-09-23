"""foolproof-operation：Python 依赖库安装器（tkinter GUI + CLI）+ 环境发现（NTFS USN/MFT 索引）。

包内结构：

- ``core``：Cordis 式可逆插件内核（Context / effect 逆序回收 / fork / 事务化清单装配）
  + ``runtime``（装配入口）、``tasklog``（JSONL 操作流水）、``audit``（启动审计）；
- ``index``：只读 USN/MFT 索引（ctypes 直调；**不创建 USN journal**）；
- ``plugins``：功能插件行（categories / pip / reqtxt / installer / search / scanner）；
- ``cli``：命令行接口；``gui``：tkinter 界面。

随包分发的资源：``requirements_categories.json``（分类清单）、
``foolproof.composition.json``（插件清单）、``tools/everything/``（内置 es.exe，MIT © voidtools）。
"""

from __future__ import annotations

__version__ = "1.1.0"
__all__ = ["__version__"]
