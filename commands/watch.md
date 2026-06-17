---
description: 自选股盯盘速览(刷新最新分时/触发状态)
allowed-tools: Bash(python -m analyzer.run:*), Read
---

刷新自选盯盘并读取最新状态:

!`python -m analyzer.run --force`

然后读取 `docs/data/data.json`,逐只播报自选股:当前价、区间涨跌幅、分时斜率、量比、是否触发异动(涨跌幅/斜率激增/放量/突破)。
只客观播报盘面,不给买卖建议。如有触发,说明触发了哪条规则、是什么客观现象。
