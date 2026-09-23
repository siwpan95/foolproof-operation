"""兼容入口（仓库根）：``python requirements.py`` / ``python requirements.py cli <子命令>``。

实现已迁入 ``foolproof_operation`` 包（``foolproof_operation/gui.py``、``cli.py``）；
本 shim 只把仓库根加入 ``sys.path`` 再转交，因此**未安装包时也能直接用**。

等价命令::

    python -m foolproof_operation              # 图形界面
    python -m foolproof_operation cli list     # 命令行
    foolproof-operation cli list               # 安装后（console script）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foolproof_operation.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())