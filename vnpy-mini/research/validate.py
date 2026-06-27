"""
验证层 —— 区分"真 edge"和"过拟合幻觉"的地方。散户最缺、也最该有的一层。

提供：
  * walk_forward_ic：分段（样本外）检验因子 IC 是否稳定，而非只在全样本好看。
  * parameter_sensitivity：扫参数看绩效是"高原"还是"尖峰"——尖峰几乎一定是过拟合。
  * deflated_note：多次试验后，最好成绩会被运气抬高（数据窥探），给出告诫。
"""

from __future__ import annotations

from typing import Callable, Dict, List

import numpy as np
import pandas as pd

from .backtest import AShareCosts, run_backtest
from .factors import information_coefficient


def walk_forward_ic(factor: pd.DataFrame, fwd_ret: pd.DataFrame, n_folds: int = 4) -> List[Dict]:
    """把时间轴切成 n_folds 段，分别算 IC。各段都正、符号一致 = 稳定；忽正忽负 = 不可信。"""
    dates = list(factor.index)
    fold = len(dates) // n_folds
    out = []
    for i in range(n_folds):
        seg = dates[i * fold:(i + 1) * fold] if i < n_folds - 1 else dates[i * fold:]
        ic = information_coefficient(factor.loc[seg], fwd_ret.loc[seg])
        out.append({"fold": i + 1, "start": str(seg[0].date()), "end": str(seg[-1].date()),
                    "ic_mean": ic["ic_mean"], "icir": ic["icir"]})
    return out


def parameter_sensitivity(
    score: pd.DataFrame,
    panel: Dict[str, pd.DataFrame],
    top_n_grid: List[int],
    rebalance_grid: List[int],
    costs: AShareCosts = AShareCosts(),
) -> pd.DataFrame:
    """对 (top_n, rebalance_days) 网格跑回测，输出各组合的夏普。

    读法：若四周参数的夏普都还不错（一片"高原"）→ 策略稳健；
          若只有某一格特别高、周围一塌糊涂（一根"尖峰"）→ 大概率过拟合，别信。
    """
    rows = []
    for tn in top_n_grid:
        for rb in rebalance_grid:
            res = run_backtest(score, panel, top_n=tn, rebalance_days=rb, costs=costs)
            rows.append({"top_n": tn, "rebalance_days": rb,
                         "sharpe": res.stats.get("sharpe", float("nan")),
                         "ann_return": res.stats.get("ann_return", float("nan"))})
    df = pd.DataFrame(rows)
    return df.pivot(index="top_n", columns="rebalance_days", values="sharpe")


def deflated_note(n_trials: int, best_sharpe: float, n_days: int,
                  periods_per_year: int = 252) -> str:
    """对"试了 n_trials 次取最好"给出过拟合告诫（Deflated Sharpe 的直觉版）。

    原理：年化夏普的 t 统计量 ≈ SR * sqrt(年数)；纯噪声下 n 次尝试里最大 t 的期望 ≈
    sqrt(2 ln n)。反推出一个"噪声门槛夏普" hurdle = sqrt(2 ln n) / sqrt(年数)，
    只有明显超过它、且样本外可复现，才不太可能是数据窥探的运气。
    """
    years = max(n_days / periods_per_year, 1e-6)
    hurdle = np.sqrt(2 * np.log(max(n_trials, 2))) / np.sqrt(years)
    verdict = "✓ 高于噪声门槛" if best_sharpe > hurdle else "✗ 未明显超过噪声门槛，警惕过拟合"
    return (f"你扫了 {n_trials} 组参数、样本约 {years:.1f} 年。即便毫无 edge，纯靠运气"
            f"最好那组的夏普也可能到 ~{hurdle:.2f}（噪声门槛）。当前最好夏普 "
            f"{best_sharpe:.2f} → {verdict}。务必再用样本外/实盘小仓确认。")
