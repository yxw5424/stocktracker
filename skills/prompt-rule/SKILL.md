---
name: prompt-rule
description: 把用户用大白话写的 A 股盯盘规则,翻译成可计算的硬指标 DSL,回译确认,并在当前行情上核对是否接近命中。用于"自然语言提示词规则"。当用户描述一个盯盘条件、或想在某种盘面出现时被提醒时使用。
---

# 自然语言盯盘规则引擎(两段式)

核心原则(来自项目 PRD):**把"聪明"放配置期,把"确定"放运行期。** 你在配置期帮用户把大白话拆解、回译、核对;运行期优先纯硬指标(便宜、确定),语义判断是受限的实验。

## 一、解析为硬指标 DSL(第一段:确定性、便宜、可量化)

可用硬指标(封闭词表,只能从中选,不要发明):
- `volume_ratio` 量比(vs ma5 / ma20)
- `price_breakout` 突破(箱体 box_high_Nd / 均线 / 前高)
- `pct_change` 区间涨跌幅
- `intraday_shape` 分时形态(`high_open_low_close` 高开低走 / `surge` 加速拉升 / `dump` 跳水)
- `slope` 分时斜率(支持"斜率激增")
- `limit_status` 涨跌停 / 炸板(**标注:分钟级,瞬时过程可能滞后**)
- `turnover` 换手、`gap` 跳空、`ma_cross` 均线交叉、`amount` 成交额

填成 JSON:
```json
{
  "raw_nl": "<原话>",
  "symbol_scope": ["sh600909"],
  "hard_filter": {"logic": "AND", "conditions": [
    {"indicator": "volume_ratio", "op": ">=", "value": 2.0, "window": "vs_ma20"},
    {"indicator": "price_breakout", "op": ">", "ref": "box_high_20d"}
  ]},
  "noise_control": {"cooldown_sec": 3600, "max_alerts_per_day": 3}
}
```

## 二、语义判断(第二段:实验,默认关闭)

"是否见光死 / 假突破 / 利好已 price-in"这类需要语义+情绪的判断:
- **默认不做**。理由:金融 LLM 方向预测准确率仅约 45–53%(略高于抛硬币),且对特定个股给方向判断属**证券投顾红线**。
- 用户坚持时:明确标注「AI 实验判断,可能误差大,非投资建议」,只罗列客观证据(分时形态、资金、近期新闻),**不下"会涨/会跌"结论**。

## 三、回译 + 核对(必做)

1. **回译**:用大白话说"系统理解成了什么",并**逐条列出被量化的阈值与默认值**(例:"'最近'默认=5 个交易日,可改";"'放量'默认=量比≥2")。让用户能发现分歧。
2. **核对**:运行 `python -m analyzer.digest`(市场+自选)或 `python -m analyzer.screen --top 50`(全市场),指出当前哪些票接近命中该规则的硬指标。
3. **降噪建议**:给冷却时间与每日上限,避免刷屏(误报是这类工具的生死线)。

## 四、落地到运行期引擎(rules.yaml)

确认无误后,把规则按 `rules.yaml` 的结构追加进去 —— 运行期由 `analyzer/rules.py` **确定性执行**(无 LLM、毫秒级、可复算),命中会带客观事实推送并显示在看板:

```yaml
- id: r_xxx
  name: 规则名
  raw_nl: "用户原话"
  scope: ["*"]            # 或 ["600909"]
  enabled: true
  shadow: true            # 新规则先影子观察 1~2 天,再改 false 放量
  logic: AND              # AND / OR
  conditions:
    - {indicator: vol_ratio, op: ">=", value: 2.0}
    - {indicator: price_breakout, op: ">", ref: box_high_20d}
  noise: {cooldown_sec: 3600, max_alerts_per_day: 2}
```

- **影子模式**:`shadow: true` 只记录不推送,看板显示"如果开了会推几条",符合 PRD 的灰度上线。
- **历史回放(PIT 对齐)**:`python -m analyzer.rules --replay <id> --code <代码>` 看这条规则历史触发点与后续收益;
  `slope/intraday_shape` 等分时级条件日线无法复现,会被**诚实标注并忽略**,绝不假装能回测。
- 用 `python -m analyzer.rules` 可随时列出全部规则及阈值。

## 铁律

- 只输出**客观事实**(放量 X 倍、突破近 N 日箱体、高开低走),**绝不输出方向性结论**(会涨 / 见光死 / 该买 / 抄底 / 目标价)。
- 每次结论附「不构成投资建议」。
- 用户表达"满仓 / 梭哈 / 追涨"冲动时,按"高波动单票=高破产风险"提醒控制仓位。
- 复杂的否定 / 时序 / 组合规则容易解析错 → 引导用户**拆成多条小而清晰的规则**。
