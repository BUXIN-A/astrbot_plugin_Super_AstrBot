"""时间衰减的统一口径。

半衰期衰减在项目里被反复使用（记忆检索打分、风格样本维护、图谱权重、好感度回归），
公式相同但写法散落各处，容易出现「某处忘了钳制半衰期」这类不一致。这里收敛为
一个纯函数，调用方只需提供半衰期与经过天数。
"""

from __future__ import annotations

_MIN_HALF_LIFE = 1.0


def half_life_factor(
    half_life_days: float,
    *,
    elapsed_days: float = 1.0,
    minimum_half_life: float = _MIN_HALF_LIFE,
) -> float:
    """返回 ``0.5 ** (elapsed_days / half_life_days)``。

    典型用法：按日维护时取 ``elapsed_days=1.0``，得到「每天乘以的比例」，
    连续乘 ``half_life_days`` 次后权重恰好减半。

    Args:
        half_life_days: 半衰期（天）；小于 ``minimum_half_life`` 时按下限处理。
        elapsed_days: 经过天数；负数按 0 处理。
        minimum_half_life: 半衰期下限，避免极小值把系数压成 0。
    """
    half_life = max(minimum_half_life, float(half_life_days or minimum_half_life))
    return 0.5 ** (max(0.0, float(elapsed_days)) / half_life)


__all__ = ["half_life_factor"]
