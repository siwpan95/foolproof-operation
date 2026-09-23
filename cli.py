"""兼容入口（仓库根）：``python cli.py <子命令>``；并 re-export 包内 ``cli`` 的公开名。

实现已迁入 ``foolproof_operation/cli.py``；新代码请直接 ``from foolproof_operation import cli``。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foolproof_operation.cli import *  # noqa: F401,F403,E402
from foolproof_operation.cli import build_parser, run_cli  # noqa: F401,E402

if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))