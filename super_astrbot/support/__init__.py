"""跨层共享的纯工具包（零依赖、无副作用）。

放在独立包是为了避免「storage 依赖 memory」这类反向依赖：
分词与相似度既被索引层（storage）使用，也被检索层（memory）使用。
"""

from .text import (
    build_match_query,
    jaccard,
    normalize_text,
    tokenize,
    truncate,
)
from .tokens import (
    MESSAGE_OVERHEAD,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)

__all__ = [
    "tokenize",
    "build_match_query",
    "jaccard",
    "normalize_text",
    "truncate",
    "MESSAGE_OVERHEAD",
    "estimate_tokens",
    "estimate_message_tokens",
    "estimate_messages_tokens",
]
