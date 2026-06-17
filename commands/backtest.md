---
description: 对某只股票做信号轻量回测(历史胜率,历史≠未来)
argument-hint: <6位代码> (可加 --signal big_up_volume|breakout_20d|limit_up)
allowed-tools: Bash(python -m analyzer.backtest:*)
---

对股票 $ARGUMENTS 做信号轻量回测:

!`python -m analyzer.backtest $ARGUMENTS`

请解读:该信号历史上出现后,各持有期(1/3/5/10 天)的胜率与平均收益如何?
**务必强调**:
- 样本量大小(occurrences;太少则统计无意义);
- 收益离散度(best vs worst,通常很大);
- "历史 ≠ 未来、未计交易成本/滑点/涨跌停、胜率高 ≠ 该买"。

不要据此给买卖建议,只做客观统计解读。
