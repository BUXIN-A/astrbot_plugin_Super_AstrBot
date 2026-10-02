"""内容级去重工具：内容指纹与相似判定。

移植自 memory_beyond 的「待写入记忆」去重方案，用于根治同一条事实在
不同反思轮次 / 不同写入来源被反复落库的重复记忆问题：

- ``content_fingerprint``：同一事实（同类型+同正文）恒定得到同一指纹，
  跨反思轮次稳定，用于**精确去重**（指纹相同直接复用已有记忆）；
- ``similar_enough``：存在包含关系，或字符 2-gram（bigram）Jaccard
  重叠率 ≥ 阈值，即判为同一事实，用于**近义去重**（命中则就地更新
  已有记忆，不另建新行）。

为什么用字符 2-gram 而非分词：中文没有词边界，分词后一句话往往只剩
寥寥几个长 token，Jaccard 几乎全 0，近义改写会漏判；2-gram 才能捕获
「用户本地文献库搭建」这类前缀重叠的近义重复。
"""

from __future__ import annotations

import hashlib
import re

SIMILAR_THRESHOLD = 0.5
"""相似判重阈值（精度优先）：0.4 会把「用户喜欢苹果」误并「用户喜欢橘子」
（2-gram Jaccard≈0.43）；0.5 仍能正确区分。语料更长、事实描述更详细时可
上调到 0.6~0.7 更保守（但会放过更多近义改写）。"""

# 停用词：归一化时剔除，降低中英文噪声对指纹/相似度的影响。
_STOPWORDS = frozenset(
    "的 了 是 在 我 你 他 她 它 们 这 那 有 和 与 及 一个 用户 本地 文献 库 "
    "搭建 完成 尚未 已经 进行 中 关于 我们 你们 他们 自己 这个 那个".split()
)


def _norm_text(s: str) -> str:
    """归一化：小写、压空白，剔停用词，只保留字母数字与中文字符。"""
    s = re.sub(r"[\s_]+", " ", str(s or "").lower())
    return " ".join(t for t in re.findall(r"[\w\u4e00-\u9fff]+", s) if t not in _STOPWORDS)


def content_fingerprint(content: str, mem_type: str = "") -> str:
    """内容指纹：同一事实（类型+正文）恒定得到同一串，跨反思轮次稳定。

    ``mem_type`` 参与指纹：同正文不同类型（fact / insight / preference）
    不算精确重复，会落到相似判定由内容决定是否合并。
    """
    payload = f"{mem_type}|{content or ''}"
    return hashlib.sha1(_norm_text(payload).encode("utf-8")).hexdigest()[:16]


def _shingles(s: str, n: int = 2) -> set[str]:
    s = _norm_text(s)
    if len(s) <= n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def similar_enough(a: str, b: str, threshold: float = SIMILAR_THRESHOLD) -> bool:
    """内容级相似判定：包含关系 或 字符 2-gram 重叠率 ≥ 阈值 即视为同一事实。

    已知取舍：重度改写（如「尚未完成」vs「还没弄完」，Jaccard≈0.375）会漏判，
    此时靠内容指纹的精确去重兜底；不要为个别边角把阈值降到误并区间。
    """
    a, b = a or "", b or ""
    if not a or not b:
        return False
    aa, bb = a.strip(), b.strip()
    if aa in bb or bb in aa:
        return True
    sa, sb = _shingles(aa), _shingles(bb)
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= threshold
