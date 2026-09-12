"""主动消息的提示词。

主动消息会直接出现在聊天里，因此提示词必须约束三件事：

1. **像正常聊天一样说话**：不得自称「主动」「定时」「系统提示」；
2. **不提问式轰炸**：只发一句，避免连珠炮追问；
3. **不重复**：明确给出上一条主动内容，要求换一个话题或角度。
"""

from __future__ import annotations

PROACTIVE_SYSTEM = (
    "你是一个正在和用户聊天的助手。你需要基于给定的背景素材，自然地发起一句简短的闲聊。"
    "严禁提及「主动」「定时」「系统」「根据记忆」等字样，也不要解释你为什么发这条消息。"
    "严禁编造素材中没有的具体事实。"
)

_DAILY_TEMPLATE = """现在是 {period}，你想主动发一条消息给「{target}」，开启一段轻松的对话。

可参考的背景素材（可能不完整，仅供找话题）：
<material>
{material}
</material>
{avoid}
要求：
1. 只输出一句中文，不超过 {max_chars} 字，口语化、自然；
2. 可以问候、关心或提起素材中的某个话题，但不要罗列素材内容；
3. 不要提问式连续追问，最多包含一个问题；
4. 只输出这句话本身，不要引号、不要任何前后缀或解释。
"""

_IDLE_TEMPLATE = """「{target}」已经有一段时间没有说话了，你想顺着之前的聊天自然地搭一句话。

可参考的背景素材（可能不完整，仅供找话题）：
<material>
{material}
</material>
{avoid}
要求：
1. 只输出一句中文，不超过 {max_chars} 字，口语化、自然；
2. 轻轻接续上次聊到的事或关心近况，不要像客服一样问候；
3. 不要提问式连续追问，最多包含一个问题；
4. 只输出这句话本身，不要引号、不要任何前后缀或解释。
"""

_AVOID_TEMPLATE = """
你上一条主动消息是：「{last}」，本次必须换一个话题或角度，不要重复或近似表达。
"""


def period_of_day(hour: int) -> str:
    """把小时映射为口语化的时段描述。"""
    if hour < 6:
        return "深夜"
    if hour < 11:
        return "早上"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def build_proactive_prompt(
    material: str,
    *,
    kind: str,
    target: str,
    hour: int = 10,
    max_chars: int = 80,
    last_text: str = "",
) -> str:
    """构造主动消息的生成提示词。"""
    template = _IDLE_TEMPLATE if kind == "idle" else _DAILY_TEMPLATE
    return template.format(
        material=material or "（暂无素材，请发一句自然的日常问候）",
        target=target or "对方",
        period=period_of_day(hour),
        max_chars=max_chars,
        avoid=_AVOID_TEMPLATE.format(last=last_text.strip()) if last_text.strip() else "",
    )
