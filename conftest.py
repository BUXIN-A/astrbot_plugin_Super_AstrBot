"""pytest 根配置：把插件目录加入 ``sys.path``。

这样测试里可以直接 ``import super_astrbot``，无需安装为包，也无需设置环境变量。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
