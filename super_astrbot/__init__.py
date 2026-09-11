"""Super_AstrBot 业务包。

分层约定（硬性，详见根目录 ``SPEC.md``）：

- ``spec/``      规格与契约，零副作用，可被任何层引用
- ``harness/``   AstrBot 适配层，**唯一**允许 ``import astrbot.*`` 的位置
- ``loop/``      循环控制：任务作用域、调度器、并发门闸、成本预算
- ``storage/``   持久层：连接、迁移、原子写、仓储
- 其余为业务域：``memory`` / ``journal`` / ``learning`` / ``context`` / ``commands`` / ``web``

除 ``harness`` 外，任何模块都不应直接导入 AstrBot 框架符号。
"""

from __future__ import annotations

import re
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_VERSION_PATTERN = re.compile(r"^\s*version\s*:\s*(?P<value>.+?)\s*$")


def read_plugin_version(default: str = "unknown") -> str:
    """从 ``metadata.yaml`` 读取插件版本号。

    版本号只信任 metadata.yaml 这一处来源，避免前后端硬编码导致不一致
    （参考 AstrNa 的经验：Dashboard 版本号只信 metadata.yaml）。
    """
    metadata_path = _PLUGIN_ROOT / "metadata.yaml"
    try:
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            match = _VERSION_PATTERN.match(line)
            if match:
                return match.group("value").strip().strip("\"'")
    except OSError:
        return default
    return default


__version__ = read_plugin_version()
