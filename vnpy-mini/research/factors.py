"""
因子层 —— 把原始量价数据加工成"对未来收益有预测力"的横截面信号。

这里给几个**经典且有学术支撑**的量价因子（动量、反转、低波、流动性），
并提供横截面标准化、行业/市值中性化的接口，以及衡量因子有效性的 IC 工具。

注意：真实世界里这些"教科书因子"早已大量拥挤、单独用很难赚钱——
价值在于**如何组合、如何中性化、如何在严谨验证下筛选**，这正是平台要支撑的。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# 横截面工具
# --------------------------------------------------------------------------- #
def cs_zscore(wide: pd.DataFrame, winsorize: float = 3.0) -> pd.DataFrame:
    """逐日（按行）做横截面 z-score，并对极值做 winsorize，降低离群点影响。"""
    mu = wide.mean(axis=1)
    sd = wide.std(axis=1).replace(0, np.nan)
    z = wide.sub(mu, axis=0).div(sd, axis=0)
    return z.clip(-winsorize, winsorize)


def cs_rank(wide: pd.DataFrame) -> pd.DataFrame:
    """逐日横截面分位（0~1），对非正态因子更稳健。"""
    return wide.rank(axis=1, pct=True)


def neutralize(factor: pd.DataFrame, *exposures: pd.DataFrame) -> pd.DataFrame:
    """逐日把因子对若干暴露（如市值、行业哑变量）做横截面回归取残差，去掉共线影响。

    这里给一个通用的最小二乘残差实现（截距 + 各 exposure）。
    真实使用时常对 log 市值、行业 one-hot 做中性化。
    """
    if not exposures:
        return factor
    out = factor.copy() * np.nan
    for d in factor.index:
        y = factor.loc[d]
        X_cols = [np.ones(len(y))] + [e.loc[d].values for e in exposures]
        X = np.column_stack(X_cols)
        mask = np.isfinite(y.values) & np.all(np.isfinite(X), axis=1)
        if mask.sum() < X.shape[1] + 2:
            continue
        beta, *_ = np.linalg.lstsq(X[mask], y.values[mask], rcond=None)
        resid = y.values - X @ beta
        out.loc[d] = np.where(mask, resid, np.nan)
    return out


# --------------------------------------------------------------------------- #
# 经典量价因子
# --------------------------------------------------------------------------- #
def compute_price_factors(panel: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    close = panel["close"]
    ret = close.pct_change(fill_method=None)

    momentum_20 = close / close.shift(20) - 1.0           # 中期动量
    reversal_5 = -(close / close.shift(5) - 1.0)           # 短期反转（取负）
    low_vol_20 = -ret.rolling(20).std()                    # 低波动异象（取负）
    illiq_20 = -(panel["volume"].rolling(20).mean())       # 反流动性（小盘/低换手，取负）

    return {
        "momentum_20": momentum_20,
        "reversal_5": reversal_5,
        "low_vol_20": low_vol_20,
        "illiq_20": illiq_20,
    }


def combine(factors: Dict[str, pd.DataFrame],
            weights: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """把多个因子各自横截面 z-score 后加权合成一个综合打分。"""
    weights = weights or {k: 1.0 for k in factors}
    score = None
    for name, f in factors.items():
        w = weights.get(name, 0.0)
        if w == 0:
            continue
        z = cs_zscore(f) * w
        score = z if score is None else score.add(z, fill_value=0.0)
    return score


# --------------------------------------------------------------------------- #
# 因子有效性：IC（信息系数）
# --------------------------------------------------------------------------- #
def information_coefficient(factor: pd.DataFrame, fwd_ret: pd.DataFrame,
                            method: str = "spearman") -> Dict[str, float]:
    """逐日计算因子值与次日收益的横截面相关（IC），汇总成 IC 均值 / ICIR / 胜率。

    - IC 均值 > 0.02~0.03 即算不错；ICIR = IC均值/IC标准差，衡量稳定性（>0.3 较好）。
    - 这是判断"因子到底有没有用"的第一道、也是最重要的一道关。
    """
    common = factor.index.intersection(fwd_ret.index)
    ics: List[float] = []
    for d in common:
        a, b = factor.loc[d], fwd_ret.loc[d]
        m = a.notna() & b.notna()
        if m.sum() < 10:
            continue
        x, y = a[m], b[m]
        if method == "spearman":          # 用秩相关，避免引入 scipy
            x, y = x.rank(), y.rank()
        ics.append(float(x.corr(y)))       # pearson（pandas 自带，无需 scipy）
    s = pd.Series(ics, dtype=float)
    icir = (s.mean() / s.std()) if s.std() and not np.isnan(s.std()) else float("nan")
    return {
        "ic_mean": float(s.mean()),
        "ic_std": float(s.std()),
        "icir": float(icir),
        "ic_win_rate": float((s > 0).mean()),
        "n_days": int(s.shape[0]),
        "icir_annual": float(icir * np.sqrt(252)) if np.isfinite(icir) else float("nan"),
    }
