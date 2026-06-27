"""
数据层 —— 所有研究的地基。

真实平台里，数据质量决定一切。这里提供：
  1. 一个**结构化合成面板**（synthetic panel），其中嵌入了一个"真实可预测"的隐藏
     信号 alpha_true：它在 t 时刻可观测、并真的与 t+1 的收益相关。用它来验证整条
     回测/验证流水线"对真信号有反应、对噪声没反应"（见 run_research.py 的诚实性测试）。
  2. 真实 A股数据的接入约定（akshare / tushare / qlib）——见文件底部 load_real_panel。

数据统一组织成「宽表」字典：每个字段一张 (日期 × 股票) 的 DataFrame，
这样所有因子与回测都在向量化的矩阵上完成。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

# 一个面板里包含的字段
FIELDS = ["open", "high", "low", "close", "prev_close", "volume", "suspended", "alpha_true"]


def make_synthetic_panel(
    n_symbols: int = 80,
    n_days: int = 500,
    alpha: float = 0.0015,
    noise: float = 0.025,
    seed: int = 7,
    suspend_prob: float = 0.008,
) -> Dict[str, pd.DataFrame]:
    """构造嵌入已知 alpha 的合成 A股面板。

    设计要点（这是诚实性的关键）：
    - 隐藏特征 char 服从 AR(1)，具有持续性，模拟真实因子的"慢变"。
    - 其横截面 z-score = alpha_true，**在 t 时刻可观测**。
    - 次日个股超额收益 = alpha * alpha_true[t] + 市场 + 特质噪声，
      即 alpha_true[t] 真正预测 t+1，但被噪声大幅淹没（信噪比低，像真实市场）。
    回测若能在扣成本后从 alpha_true 提取出正收益、却无法从打乱后的版本提取，
    就证明这套流水线"只奖励真信号、不制造虚假 alpha"。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    symbols = [f"{600000 + i:06d}.SH" for i in range(n_symbols)]

    # 1) 持续性隐藏特征 AR(1)
    char = np.zeros((n_days, n_symbols))
    char[0] = rng.standard_normal(n_symbols)
    for t in range(1, n_days):
        char[t] = 0.95 * char[t - 1] + 0.32 * rng.standard_normal(n_symbols)
    sig = (char - char.mean(1, keepdims=True)) / (char.std(1, keepdims=True) + 1e-9)

    # 2) 市场因子 + 由 sig[t] 驱动的次日收益
    mkt = 0.0002 + 0.011 * rng.standard_normal(n_days)
    ret = np.zeros((n_days, n_symbols))
    ret[0] = mkt[0] + noise * rng.standard_normal(n_symbols)
    for t in range(n_days - 1):
        # alpha 远小于 noise：信噪比低、IC 仅 ~0.05，贴近真实市场
        ret[t + 1] = mkt[t + 1] + alpha * sig[t] + noise * rng.standard_normal(n_symbols)

    # 3) 由收益反推价格序列（保证 OHLC 自洽）
    close = 10.0 * np.exp(np.cumsum(ret, axis=0))
    prev_close = np.vstack([close[0:1], close[:-1]])
    open_ = prev_close * (1 + 0.4 * ret)
    high = np.maximum(open_, close) * (1 + np.abs(0.004 * rng.standard_normal((n_days, n_symbols))))
    low = np.minimum(open_, close) * (1 - np.abs(0.004 * rng.standard_normal((n_days, n_symbols))))
    volume = rng.lognormal(15.0, 0.5, (n_days, n_symbols))
    suspended = rng.random((n_days, n_symbols)) < suspend_prob
    volume[suspended] = 0.0

    def wide(a):
        return pd.DataFrame(a, index=dates, columns=symbols)

    return {
        "open": wide(open_), "high": wide(high), "low": wide(low), "close": wide(close),
        "prev_close": wide(prev_close), "volume": wide(volume),
        "suspended": wide(suspended), "alpha_true": wide(sig),
    }


def forward_return(close: pd.DataFrame) -> pd.DataFrame:
    """t 行 = 持有到 t+1 的收益（close[t+1]/close[t]-1）。仅用于 IC 分析，不喂回测。"""
    return close.shift(-1) / close - 1.0


def daily_return(close: pd.DataFrame) -> pd.DataFrame:
    """t 行 = 当日收益（close[t]/close[t-1]-1），回测中"在 t 日实现"的收益。"""
    return close.pct_change(fill_method=None).fillna(0.0)


# --------------------------------------------------------------------------- #
# 接入真实 A股数据：把下面任一数据源整理成与 make_synthetic_panel 相同的宽表字典即可。
# --------------------------------------------------------------------------- #
def load_real_panel(*args, **kwargs) -> Dict[str, pd.DataFrame]:
    """占位：把真实数据整理成 {field: 日期×股票 宽表}。

    推荐数据源（免费/低成本）：
      - akshare：ak.stock_zh_a_hist(symbol, adjust="hfq")  后复权日线
      - tushare：pro.daily / pro.adj_factor / pro.suspend_d  停牌
      - qlib：   D.features(...) 已是 point-in-time、复权对齐的高质量数据（最省心）

    关键要求（否则回测必然失真）：
      * 必须**后复权**，否则除权日产生假跳空；
      * 必须包含**退市/ST**股票的历史，否则幸存者偏差让回测虚高；
      * 成分股要 **point-in-time**（用当时的指数成分，而非今天的）。
    """
    raise NotImplementedError(
        "请按 docstring 把 akshare/tushare/qlib 数据整理成宽表字典后返回；"
        "字段需包含: " + ", ".join(FIELDS[:-1])
    )
