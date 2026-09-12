"""自动审核待审记录领域。

对外出口：

- ``AutoReviewService`` 按批扫描队列，规则优先、模型兜底地给出并落地结论
- ``ReviewConfig``      自动审核参数
- ``ReviewOutcome``     单次扫描统计
- ``build_prompt`` / ``parse_verdict`` 提示词构造与结构化解析

本域只依赖 ``ReviewRepository``、注入的审批回调与可选的 LLM 网关，不 import ``astrbot``。
"""

from .config import ReviewConfig
from .prompts import build_prompt, parse_verdict
from .service import AutoReviewService, ReviewOutcome

__all__ = [
    "ReviewConfig",
    "ReviewOutcome",
    "AutoReviewService",
    "build_prompt",
    "parse_verdict",
]
