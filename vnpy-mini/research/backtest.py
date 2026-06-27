"""
回测引擎 —— 平台的良心所在。

它的全部价值在于"不让你骗自己"：
  * 无未来函数：t 日收盘后用 ≤t 的信息决策，仓位在 t+1 才生效并赚取 t+1 的收益。
  * A股现实：T+1（最小持有≥1日）、涨跌停不可成交、停牌不可交易。
  * 真实成本：双边佣金 + 卖出印花税 + 滑点，按换手率扣减。
一个忽略这些的回测，曲线再漂亮都是幻觉。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass
class AShareCosts:
    commission: float = 0.00025   # 单边佣金（万2.5，含规费近似）
    stamp: float = 0.0005         # 印花税：仅卖出，0.05%（2023-08 后）
    slippage: float = 0.0010      # 单边滑点（10bp，偏保守）
    limit_pct: float = 0.10       # 涨跌停幅度（主板 10%；科创/创业板可设 0.20）

    def roundtrip_rate(self) -> float:
        """单位单边换手对应的成本率：买(佣金+滑点) + 卖(佣金+滑点+印花)。"""
        return 2 * self.commission + 2 * self.slippage + self.stamp


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    turnover: pd.Series
    n_holdings: pd.Series
    stats: Dict[str, float] = field(default_factory=dict)


def _buyable(panel: Dict[str, pd.DataFrame], limit_pct: float) -> pd.DataFrame:
    """t 日是否"可买入"：未停牌 且 未涨停（涨停时买不到）。"""
    close, prev = panel["close"], panel["prev_close"]
    limit_up = close >= prev * (1 + limit_pct) - 1e-9
    return (~panel["suspended"]) & (~limit_up)


def run_backtest(
    score: pd.DataFrame,
    panel: Dict[str, pd.DataFrame],
    top_n: int = 15,
    rebalance_days: int = 5,
    costs: AShareCosts = AShareCosts(),
) -> BacktestResult:
    """横截面多头组合回测：每 rebalance_days 调仓，选综合打分最高的 top_n 只等权持有。

    时序对齐（无未来函数的核心）：
      决策日 d 用 score[d]（≤ d 收盘信息）→ 在下一交易日 ex 设为目标仓位 →
      从 ex 起赚取 ex 当日及之后的收益，直到下一次调仓。换手成本在 ex 当日扣除。
    """
    close = panel["close"]
    dates = list(close.index)
    symbols = list(close.columns)
    ret = close.pct_change(fill_method=None).fillna(0.0)
    buyable = _buyable(panel, costs.limit_pct)

    # 预先在每个"执行日"放好目标权重（由前一交易日的决策得到）
    target_by_exec: Dict[pd.Timestamp, pd.Series] = {}
    for p in range(0, len(dates) - 1, rebalance_days):
        d, ex = dates[p], dates[p + 1]
        sc = score.loc[d].dropna()
        ok = buyable.loc[ex].reindex(sc.index).fillna(False)
        sc = sc[ok]
        picks = sc.nlargest(top_n).index
        tw = pd.Series(0.0, index=symbols)
        if len(picks):
            tw[picks] = 1.0 / len(picks)
        target_by_exec[ex] = tw

    w = pd.Series(0.0, index=symbols)     # 当前权重
    equity = 1.0
    rate = costs.roundtrip_rate()
    eq_idx, eq_val, ret_val, turn_val, nhold = [], [], [], [], []

    for d in dates:
        cost = 0.0
        if d in target_by_exec:
            tw = target_by_exec[d]
            oneway = float((tw - w).abs().sum()) / 2.0   # 单边换手
            cost = oneway * rate
            w = tw
            turn_val.append((d, oneway))
        r_gross = float((w * ret.loc[d]).sum())
        r_net = r_gross - cost
        equity *= (1.0 + r_net)

        eq_idx.append(d); eq_val.append(equity); ret_val.append(r_net)
        nhold.append((d, int((w > 1e-9).sum())))

        # 权重按当日收益自然漂移（保持组合权重含义）
        denom = 1.0 + r_gross
        if denom > 0:
            w = w * (1.0 + ret.loc[d]) / denom

    equity_s = pd.Series(eq_val, index=eq_idx, name="equity")
    returns_s = pd.Series(ret_val, index=eq_idx, name="returns")
    turnover_s = pd.Series(dict(turn_val), name="turnover")
    nhold_s = pd.Series(dict(nhold), name="n_holdings")

    return BacktestResult(
        equity=equity_s, returns=returns_s, turnover=turnover_s,
        n_holdings=nhold_s, stats=performance_stats(returns_s, turnover_s),
    )


def latest_picks(score: pd.DataFrame, panel: Dict[str, pd.DataFrame],
                 top_n: int = 15, costs: AShareCosts = AShareCosts()) -> List[Dict]:
    """取最新一日的综合打分，输出"可买入"的 top_n 选股，作为给执行端的信号。

    返回 [{symbol, score, weight}]，可直接喂给 vnpy-mini 下单（见 research/README.md）。
    """
    d = score.index[-1]
    sc = score.loc[d].dropna()
    ok = _buyable(panel, costs.limit_pct).loc[d].reindex(sc.index).fillna(False)
    sc = sc[ok].nlargest(top_n)
    w = 1.0 / len(sc) if len(sc) else 0.0
    return [{"symbol": s, "score": round(float(v), 4), "weight": round(w, 4)}
            for s, v in sc.items()]


def performance_stats(returns: pd.Series, turnover: pd.Series | None = None,
                      periods_per_year: int = 252) -> Dict[str, float]:
    """年化收益 / 波动 / 夏普 / 最大回撤 / 卡玛 / 平均单边换手。"""
    r = returns.dropna()
    if r.empty:
        return {}
    equity = (1 + r).cumprod()
    n = len(r)
    ann_ret = equity.iloc[-1] ** (periods_per_year / n) - 1
    ann_vol = r.std() * np.sqrt(periods_per_year)
    sharpe = (r.mean() / r.std() * np.sqrt(periods_per_year)) if r.std() else float("nan")
    dd = (equity / equity.cummax() - 1).min()
    calmar = (ann_ret / abs(dd)) if dd < 0 else float("nan")
    stats = {
        "ann_return": float(ann_ret),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(dd),
        "calmar": float(calmar),
        "total_return": float(equity.iloc[-1] - 1),
        "n_days": int(n),
    }
    if turnover is not None and len(turnover):
        stats["avg_turnover"] = float(turnover.mean())
    return stats
