"""
端到端研究流水线演示 + 平台诚实性测试。

跑一遍就能看到一个"真正的策略开发平台"该有的全流程：
  1. 数据 → 2. 因子 + IC → 3. 严谨回测（含 A股成本/T+1/涨跌停）→
  4. 样本外/参数稳健性验证 → 5. 过拟合告诫。

诚实性测试（最重要）：
  - 用"真信号 alpha_true" 跑：扣成本后应有正收益、正 IC。
  - 把同一信号在横截面**打乱**后再跑：alpha 被破坏，扣成本后应≈0 甚至为负。
  两者对比 = 证明这套回测"只奖励真信号、不会无中生有"。

运行： python -m research.run_research
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D
from . import factors as F
from .backtest import AShareCosts, run_backtest
from .validate import deflated_note, parameter_sensitivity, walk_forward_ic

pd.set_option("display.width", 100)


def line(title=""):
    print("\n" + "─" * 64 + (f" {title}" if title else ""))


def show_stats(name, stats):
    print(f"  {name:<16} 年化 {stats['ann_return']*100:6.2f}%  "
          f"夏普 {stats['sharpe']:5.2f}  回撤 {stats['max_drawdown']*100:6.2f}%  "
          f"换手 {stats.get('avg_turnover', float('nan'))*100:5.1f}%")


def main():
    costs = AShareCosts()

    line("1. 数据")
    panel = D.make_synthetic_panel(n_symbols=80, n_days=600, alpha=0.0015, noise=0.025, seed=7)
    close = panel["close"]
    fwd = D.forward_return(close)
    print(f"  合成 A股面板：{close.shape[1]} 只 × {close.shape[0]} 个交易日")
    print(f"  成本设定：佣金{costs.commission*1e4:.1f}bp/边 印花{costs.stamp*1e4:.0f}bp(卖) "
          f"滑点{costs.slippage*1e4:.0f}bp/边 涨跌停±{costs.limit_pct*100:.0f}%")

    line("2. 因子有效性（IC）")
    price_factors = F.compute_price_factors(panel)
    for nm, f in price_factors.items():
        ic = F.information_coefficient(f, fwd)
        print(f"  教科书因子 {nm:<14} IC均值 {ic['ic_mean']:+.3f}  ICIR {ic['icir']:+.2f}  "
              f"胜率 {ic['ic_win_rate']*100:.0f}%")
    ic_true = F.information_coefficient(panel["alpha_true"], fwd)
    print(f"  真信号  alpha_true     IC均值 {ic_true['ic_mean']:+.3f}  ICIR {ic_true['icir']:+.2f}  "
          f"胜率 {ic_true['ic_win_rate']*100:.0f}%   ← 这是有 edge 的样子")

    line("3. 严谨回测（扣 A股真实成本）")
    score_true = F.cs_zscore(panel["alpha_true"])
    res_true = run_backtest(score_true, panel, top_n=15, rebalance_days=5, costs=costs)
    show_stats("真信号策略", res_true.stats)

    # 诚实性测试：横截面打乱信号 -> 破坏 alpha，成本不变
    rng = np.random.default_rng(0)
    arr = panel["alpha_true"].to_numpy().copy()
    for i in range(arr.shape[0]):
        rng.shuffle(arr[i])                 # 逐行（横截面）打乱，彻底破坏 alpha
    shuffled = pd.DataFrame(arr, index=panel["alpha_true"].index, columns=panel["alpha_true"].columns)
    res_fake = run_backtest(F.cs_zscore(shuffled), panel, top_n=15, rebalance_days=5, costs=costs)
    show_stats("打乱信号策略", res_fake.stats)
    print("  ↑ 诚实性检验：真信号应明显为正，打乱后应≈0/为负（被成本吃掉）。")
    print("    这正是平台的价值——回测不会把噪声美化成 alpha。")

    line("4. 样本外稳健性")
    print("  walk-forward 分段 IC（真信号，各段应同号且稳定）：")
    for f in walk_forward_ic(panel["alpha_true"], fwd, n_folds=4):
        print(f"    第{f['fold']}段 {f['start']}~{f['end']}  IC {f['ic_mean']:+.3f}  ICIR {f['icir']:+.2f}")
    print("\n  参数敏感性（夏普；看是'高原'还是'尖峰'）：")
    grid = parameter_sensitivity(score_true, panel, top_n_grid=[10, 15, 20, 30],
                                 rebalance_grid=[3, 5, 10], costs=costs)
    print(grid.round(2).to_string())

    line("5. 过拟合告诫")
    n_trials = grid.size
    print("  " + deflated_note(n_trials, float(np.nanmax(grid.values)), res_true.stats["n_days"]))

    line("结论")
    print("  这套流水线 = 数据→因子(IC)→无未来函数回测(A股成本/T+1/涨跌停)→样本外验证→防过拟合。")
    print("  它不提供'能赚钱的策略'——它提供让你诚实地找到并验证自己 edge 的基础设施。")
    print("  接真实数据：实现 research/data.py 的 load_real_panel（akshare/tushare/qlib）。")
    print("  接执行：把最后一日 score 的 top_n 选股推给 vnpy-mini 下单（见 research/README.md）。\n")


if __name__ == "__main__":
    main()
