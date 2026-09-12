"""提示词目录：把各业务域的内置提示词汇总成一份可编辑清单。

面板的「提示词」页据此渲染每项的标题、分组、说明、必填占位符与内置默认值，
后端保存时也据此校验。清单**不在这里复写默认文本**，而是直接引用各域
``prompts.py`` 的常量——提示词只有一处定义，改内置默认不会漏改面板。

为什么单独放在包根：各域模块依赖 ``support``，由 ``support`` 反向引用业务域
会形成环；汇总属于应用装配层，与 ``app.py`` 同层最自然。
"""

from __future__ import annotations

from .context.prompts import PROMPT_SPECS as _CONTEXT
from .graph.config import PROMPT_SPECS as _GRAPH
from .learning.prompts import PROMPT_SPECS as _LEARNING
from .persona.prompts import PROMPT_SPECS as _PERSONA
from .proactive.prompts import PROMPT_SPECS as _PROACTIVE
from .review.prompts import PROMPT_SPECS as _REVIEW
from .support import PromptSpec

PROMPT_SPECS: tuple[PromptSpec, ...] = (
    *_LEARNING,
    *_CONTEXT,
    *_PROACTIVE,
    *_PERSONA,
    *_GRAPH,
    *_REVIEW,
)
"""全部可定制提示词，顺序即面板展示顺序。"""

BY_KEY: dict[str, PromptSpec] = {spec.key: spec for spec in PROMPT_SPECS}

__all__ = ["BY_KEY", "PROMPT_SPECS"]
