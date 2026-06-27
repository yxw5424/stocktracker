"""
组合构建与风控 —— 把"打分"变成"可执行、风险可控的权重"。

仅靠选股打分还不够，真实组合必须控制：
  * 单票上限（避免押注单一标的）
  * 行业暴露上限（避免行业 all-in）
  * 换手约束（控制交易成本、避免追涨杀跌）
另配合 factors.neutralize 做市值/行业中性化，去掉风格裸暴露。
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def _cap_weights(w: pd.Series, max_weight: float) -> pd.Series:
    """把单票权重压到 max_weight 以下，超出的部分按比例分给未触顶的票（迭代）。"""
    w = w.clip(lower=0).copy()
    if w.sum() <= 0:
        return w
    for _ in range(50):
        over = w > max_weight + 1e-12
        if not over.any():
            break
        excess = (w[over] - max_weight).sum()
        w[over] = max_weight
        room = (~over) & (w > 0)
        if not room.any():
            break                       # 没地方再分了，剩余留作现金
        w[room] += excess * (w[room] / w[room].sum())
    return w


def _cap_sectors(w: pd.Series, sector: pd.Series, max_sector_weight: float) -> pd.Series:
    """把任一行业的总权重压到 max_sector_weight 以下；超出部分留作现金（不强行塞进别的行业）。

    单纯把超限行业按比例缩到上限即可——缩小不会把其它行业顶上去，一遍到位、稳定收敛。
    若所有行业都触顶导致总仓位 < 1，剩余即为现金，这是正确且保守的结果。
    """
    w = w.copy()
    sec_w = w.groupby(sector).sum()
    for sec, total in sec_w.items():
        if total > max_sector_weight + 1e-12:
            members = sector[sector == sec].index
            w[members] *= max_sector_weight / total
    return w


def _limit_turnover(target: pd.Series, prev: pd.Series, max_turnover: float) -> pd.Series:
    """若目标相对上期换手超过 max_turnover，则只向目标移动一部分（线性插值）。"""
    target = target.reindex(prev.index.union(target.index)).fillna(0.0)
    prev = prev.reindex(target.index).fillna(0.0)
    turnover = float((target - prev).abs().sum()) / 2.0
    if turnover <= max_turnover or turnover == 0:
        return target
    alpha = max_turnover / turnover
    return prev + alpha * (target - prev)


def build_portfolio(
    score: pd.Series,
    tradable: pd.Series,
    sector: Optional[pd.Series] = None,
    prev_weights: Optional[pd.Series] = None,
    top_n: int = 20,
    max_weight: float = 0.10,
    max_sector_weight: float = 0.30,
    max_turnover: float = 0.30,
) -> pd.Series:
    """由横截面打分构建受约束的多头组合权重（和≤1，差额为现金）。

    顺序：选 top_n → 等权 → 单票封顶 → 行业封顶 → 换手约束。
    """
    ok = tradable.reindex(score.index).fillna(False) & score.notna()
    cand = score[ok].nlargest(top_n)
    if cand.empty:
        return pd.Series(dtype=float)

    w = pd.Series(1.0 / len(cand), index=cand.index)
    w = _cap_weights(w, max_weight)
    if sector is not None:
        w = _cap_sectors(w, sector.reindex(w.index).fillna("UNKNOWN"), max_sector_weight)
    if prev_weights is not None and len(prev_weights):
        w = _limit_turnover(w, prev_weights, max_turnover)
    return w[w > 1e-6]


def sector_exposure(weights: pd.Series, sector: pd.Series) -> pd.Series:
    """组合的行业暴露（各行业权重之和），用于风控检查/展示。"""
    return weights.groupby(sector.reindex(weights.index).fillna("UNKNOWN")).sum().sort_values(ascending=False)
