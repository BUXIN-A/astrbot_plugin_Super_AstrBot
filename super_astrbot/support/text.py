"""文本处理：分词、FTS 查询构造、相似度。

分词策略（分层自适应的一部分）：

- 若 ``jieba`` 可用（AstrBot 宿主已内置），使用其搜索引擎模式切分，中文效果更好；
- 否则降级为「ASCII 词 + 中文字符 bigram」，保证在没有 jieba 的环境下 FTS 仍可用。

索引与查询**必须使用同一套分词**，否则 FTS 命中率会显著下降。
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

# 极简停用词表：只去掉高频虚词，减少噪声召回，不追求完整性。
_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "是",
        "在",
        "我",
        "你",
        "他",
        "她",
        "它",
        "们",
        "和",
        "与",
        "就",
        "都",
        "也",
        "还",
        "而",
        "但",
        "或",
        "及",
        "对",
        "把",
        "被",
        "让",
        "给",
        "着",
        "过",
        "呢",
        "吗",
        "吧",
        "啊",
        "呀",
        "嗯",
        "哦",
        "这个",
        "那个",
        "什么",
        "怎么",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "of",
        "to",
        "in",
        "on",
        "and",
        "or",
        "for",
        "with",
        "at",
        "by",
        "it",
        "this",
        "that",
    }
)

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_RUN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+")

_jieba_module: object | None = None
_jieba_ready = False


def _get_jieba() -> object | None:
    """惰性加载 jieba，并把其日志压到 WARNING 以下（避免刷屏）。"""
    global _jieba_module, _jieba_ready
    if _jieba_ready:
        return _jieba_module
    _jieba_ready = True
    try:
        import jieba  # type: ignore

        try:
            jieba.setLogLevel(logging.WARNING)
        except Exception:  # noqa: BLE001 - 日志级别设置失败无关紧要
            pass
        _jieba_module = jieba
    except Exception:  # noqa: BLE001 - 无 jieba 时走 bigram 降级
        _jieba_module = None
    return _jieba_module


def normalize_text(text: str) -> str:
    """统一空白、去掉零宽字符（防注入与检索噪声）。"""
    if not text:
        return ""
    cleaned = (
        text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_meaningful(token: str) -> bool:
    if not token:
        return False
    if token in _STOPWORDS:
        return False
    # 纯标点/空白丢弃
    return bool(_CJK_RE.search(token) or _ASCII_WORD_RE.search(token))


def _tokenize_with_jieba(text: str) -> list[str]:
    module = _get_jieba()
    if module is None:
        return []
    try:
        pieces = module.cut(text, cut_all=False)  # type: ignore[attr-defined]
        return [piece.strip().lower() for piece in pieces]
    except Exception:  # noqa: BLE001 - 分词失败即降级
        return []


def _tokenize_fallback(text: str) -> list[str]:
    tokens: list[str] = []
    for run in _TOKEN_RUN_RE.findall(text):
        if _ASCII_WORD_RE.fullmatch(run):
            tokens.append(run.lower())
            continue
        if len(run) == 1:
            tokens.append(run)
            continue
        # 中文 bigram，保证无词典时也能命中子串
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def tokenize(text: str, *, max_tokens: int = 128) -> list[str]:
    """分词并去重，保持原有顺序。

    Args:
        text: 原文。
        max_tokens: 单条文本最多保留的词数，防止超长文本撑大索引。
    """
    normalized = normalize_text(text)
    if not normalized:
        return []

    candidates = _tokenize_with_jieba(normalized)
    if not candidates:
        candidates = _tokenize_fallback(normalized)

    seen: set[str] = set()
    result: list[str] = []
    for token in candidates:
        if not _is_meaningful(token) or token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= max_tokens:
            break
    return result


def build_match_query(tokens: Iterable[str]) -> str:
    """把词元列表构造成 FTS5 MATCH 表达式（OR 连接，逐词加引号转义）。"""
    parts: list[str] = []
    for token in tokens:
        if not token:
            continue
        escaped = token.replace('"', '""')
        parts.append(f'"{escaped}"')
    return " OR ".join(parts)


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """词袋 Jaccard 相似度，用于召回结果去重。"""
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """按字符截断（不含 emoji 宽度修正，够用即可）。"""
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)] + suffix
