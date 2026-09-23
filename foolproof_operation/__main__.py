"""``python -m foolproof_operation`` 的统一入口。

- 无参数 / 未知参数 → 打开图形界面（与历史行为一致）；
- ``cli <子命令> …`` → 命令行模式（等价于 ``foolproof-operation cli …``）；
- ``--python <exe>`` → 指定 GUI 操作的目标解释器（由 ``gui`` 模块读取 ``sys.argv`` 推导）。

仓库根的 ``requirements.py`` 是同一入口的兼容 shim（老命令继续可用）。
"""

from __future__ import annotations

import sys
from typing import List, Optional

__all__ = ["main"]


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "cli":
        from .cli import run_cli

        return run_cli(args[1:])
    from .gui import InstallerApp

    InstallerApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
