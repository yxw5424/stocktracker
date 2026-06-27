# research —— A股横截面多因子研究层

这是把"玩具策略"升级成**真正策略开发平台**的核心层。它不给你"能赚钱的策略"
（公开且有效的策略不存在），而是给你一套**让你诚实地找到并验证自己 edge** 的基础设施。

> 一句话：界面（vnpy-mini）是执行与监控的"脸"，这一层才是研究的"脑"。

## 为什么这层最重要

散户回测漂亮、实盘亏损，几乎都死在这四件事上。本层在工程上**强制**处理它们：

| 致命陷阱 | 本层如何处理 | 代码位置 |
|---|---|---|
| 未来函数（lookahead） | t 日收盘信号 → t+1 才生效并赚 t+1 收益，时序错位由构造保证 | `backtest.run_backtest` |
| 忽略成本 | 双边佣金 + 卖出印花税 + 滑点，按换手扣减；A股 T+1、涨跌停不可成交、停牌不可交易 | `backtest.AShareCosts` / `_buyable` |
| 因子无效却自我感动 | IC / ICIR / 胜率，先过这关再谈回测 | `factors.information_coefficient` |
| 过拟合 / 数据窥探 | 样本外分段 IC、参数敏感性（高原 vs 尖峰）、噪声门槛夏普 | `validate.py` |

## 跑一遍看全流程

```bash
cd vnpy-mini
pip install numpy pandas
python -m research.run_research
```

输出包含一个**诚实性测试**：
- 用"真信号 alpha_true"回测 → 扣成本后夏普 ~2.2；
- 把同一信号横截面**打乱**后回测 → 夏普 ~0.2（alpha 被破坏、被成本吃掉）。

这个对比就是平台的价值证明：**回测只奖励真信号，不会把噪声美化成 alpha。**

## 模块

```
research/
├── data.py       # 数据层：合成面板(含已知alpha) + 真实数据接入约定
├── factors.py    # 因子层：经典量价因子 + 横截面标准化/中性化 + IC
├── backtest.py   # 回测引擎：无未来函数 + A股成本/T+1/涨跌停/停牌
├── validate.py   # 验证层：walk-forward IC + 参数敏感性 + 过拟合告诫
└── run_research.py  # 端到端演示 + 诚实性测试
```

## 接真实 A股数据（关键一步）

合成数据只为验证流水线本身。要做真研究，实现 `data.py` 的 `load_real_panel`，
把任一数据源整理成 `{字段: 日期×股票 宽表}`：

- **akshare**（免费）：`ak.stock_zh_a_hist(symbol, adjust="hfq")` 后复权日线
- **tushare**（注册）：`pro.daily` + `pro.adj_factor` + `pro.suspend_d`
- **qlib**（推荐）：`D.features(...)` 已是 point-in-time、复权对齐的高质量数据

**三条铁律**（违反则回测必然失真）：
1. 必须**后复权**，否则除权日假跳空；
2. 必须含**退市/ST**股票历史，否则幸存者偏差让回测虚高；
3. 指数成分要 **point-in-time**（用当时成分，不是今天的）。

> 想直接上工业级 ML alpha（GBDT/神经网络 + 自动因子）→ 用 [microsoft/qlib](https://github.com/microsoft/qlib)
> 当研究大脑，它把数据/因子/训练/回测都做好了；本层可作为你理解其内部"严谨回测"
> 与"对接执行"的最小可读实现。

## 研究 → 执行：把信号接到 vnpy-mini

研究产出"今天买哪些、各多少权重"，执行交给 vnpy-mini（先 paper 再实盘）：

```python
from research import data as D, factors as F
from research.backtest import latest_picks

panel = D.load_real_panel(...)              # 你的真实数据
score = F.combine(F.compute_price_factors(panel))   # 或用你训练好的模型预测值
picks = latest_picks(score, panel, top_n=15)
# picks = [{"symbol": "600000.SH", "score": 1.83, "weight": 0.0667}, ...]

# 再按权重 * 总资金 / 价格 算手数，逐个 POST 给 vnpy-mini：
#   POST /api/order  {symbol, exchange, direction:"LONG", offset:"OPEN", price, volume}
```

建议流程：**研究层选股 → vnpy-mini 模拟盘（SimNow / paper）验证执行与滑点 →
小仓实盘 → 持续用 walk-forward 监控因子是否衰减。**

## ⚠️ 诚实声明
- 合成数据里的"漂亮夏普"是为演示流水线而设计的，**不代表任何真实收益**。
- 真实市场 IC 能到 0.03~0.05、ICIR>0.3 就已算不错的因子；单因子很难赚钱，
  价值在于**多因子组合 + 中性化 + 严谨验证 + 风控**。
- 任何回测都只是必要不充分条件。上实盘前请用样本外 + 小仓实测反复确认。
