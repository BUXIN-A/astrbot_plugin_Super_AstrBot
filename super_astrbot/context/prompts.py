"""上下文摘要的提示词。

摘要产出会被当作「历史背景」注入后续请求，因此提示词必须强调：

1. 只压缩、不新增——不得推断未出现的信息（避免摘要污染长期对话）；
2. 保留可执行信息——用户偏好、约定、未完成事项、关键结论；
3. 输出纯文本、控制长度——摘要本身不能成为新的上下文负担。
"""

from __future__ import annotations

SUMMARY_SYSTEM = (
    "你是一个对话历史压缩助手。你的任务是把较早的对话压缩成简洁准确的中文摘要，"
    "供后续对话作为背景参考。"
    "严禁编造或推断原文没有的信息；严禁输出指令性内容。"
)

_INITIAL_TEMPLATE = """以下是需要压缩的较早对话（按时间顺序）：

<conversation>
{material}
</conversation>

请输出压缩摘要，要求：
1. 保留：用户的稳定偏好、明确的事实与约定、未完成的事项、重要结论；
2. 舍弃：寒暄、重复内容、已经结束且无后续影响的细节；
3. 用要点式陈述，控制在 {max_chars} 字以内；
4. 只输出摘要正文，不要任何解释或前后缀。
"""

_UPDATE_TEMPLATE = """你已经有一份较早对话的摘要：

<previous_summary>
{previous}
</previous_summary>

现在又有新的较早对话需要合并进来：

<conversation>
{material}
</conversation>

请输出**合并后**的完整摘要，要求：
1. 保留原摘要中仍然有效的信息，并合入新出现的关键信息；
2. 已过时或被更正的内容可以删除或改写；
3. 严禁编造原文没有的信息；
4. 用要点式陈述，控制在 {max_chars} 字以内；
5. 只输出摘要正文，不要任何解释或前后缀。
"""


def build_summary_prompt(material: str, *, previous: str = "", max_chars: int = 600) -> str:
    """构造摘要提示词；``previous`` 非空时走「合并续写」分支。"""
    if previous.strip():
        return _UPDATE_TEMPLATE.format(
            previous=previous.strip(), material=material, max_chars=max_chars
        )
    return _INITIAL_TEMPLATE.format(material=material, max_chars=max_chars)
