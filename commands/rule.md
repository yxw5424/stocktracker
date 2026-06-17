---
description: 用大白话写一条盯盘规则,我帮你拆成可计算的硬指标并核对
argument-hint: <自然语言规则,如"放量突破平台就提醒我">
---

用户的盯盘规则(自然语言):**$ARGUMENTS**

请使用 **prompt-rule** 技能完成:
1. 把它解析成结构化的硬指标 DSL(用封闭词表,见技能说明);
2. 用大白话**回译**给用户确认,并列出每个被量化的阈值与默认值;
3. 运行 `python -m analyzer.digest` 或 `python -m analyzer.screen`,在当前行情上指出哪些(自选/全市场)票接近命中;
4. 给降噪建议(`cooldown_sec` 冷却、`max_alerts_per_day` 每日上限);
5. **落地**:把规则按 `rules.yaml` 的格式追加进去(新规则建议先 `shadow: true` 影子观察 1~2 天),
   并提示可用 `/rules` 查看、`/rules --replay <id> --code <代码>` 做历史回放核对。

> 运行期由 `analyzer/rules.py` 确定性执行(无 LLM、毫秒级、可复算);命中会带客观事实推送并显示在看板。

铁律:只输出客观事实判断,**绝不输出"会涨/见光死/该买/目标价"这类方向性结论**(属投顾红线);结论附"不构成投资建议"。
