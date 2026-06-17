# Claude Code 插件:stocktracker

把 A 股多维分析接进你的 Claude Code 桌面端,用斜杠命令直接调用,LLM 判断走你自己的 Claude。

## 命令

| 命令 | 作用 |
|---|---|
| `/screen` | 全市场异动扫描(异动分榜 + 板块归类) |
| `/market` | 今日市场态势(宽度 / 情绪 / 形势)+ 多维筛选信号 |
| `/watch` | 自选股盯盘速览(刷新分时 / 触发) |
| `/backtest <代码>` | 信号轻量回测(历史胜率,历史 ≠ 未来) |
| `/rule <大白话规则>` | 自然语言提示词规则 → 拆成硬指标 + 回译 + 落地到 `rules.yaml` |
| `/rules` | 列出已配置规则;`--replay <id> --code <代码>` 做历史回放(PIT 对齐) |

## 技能

- **prompt-rule**:把大白话盯盘规则翻成可计算的硬指标 DSL,两段式(硬指标确定性 + 语义判断默认关闭),只输出客观事实、不给方向建议。

## 规则引擎(运行期)

`/rule` 配置好的规则落到 `rules.yaml`,由 `analyzer/rules.py` 在每轮取数后**确定性执行**(无 LLM、毫秒级、可复算):
- 封闭词表硬指标(斜率/量比/涨跌幅/跳空/振幅/突破/分时形态),命中即带**客观事实**推送并显示在看板;
- 降噪一等公民:每条规则自带冷却 + 每日上限;
- **影子模式**(`shadow: true`):只记录不推送,先观察 1~2 天再放量;
- **历史回放**(`/rules --replay`):PIT 对齐统计触发点与后续收益,分时级条件会被诚实标注"日线无法复现"。

## 安装

本仓库根目录即一个 Claude Code 插件(`.claude-plugin/plugin.json` + `commands/` + `skills/`),并自带插件市场清单(`.claude-plugin/marketplace.json`)。

在 Claude Code 里把本仓库添加为插件市场并安装:
```
/plugin marketplace add yxw5424/stocktracker
```
然后在弹出的 `/plugin` 菜单里选 **stocktracker** 安装(或 `/plugin install stocktracker@stocktracker`)。装好后重启 Claude Code,命令即生效。

> 也可不走市场,直接本地开发加载:在项目目录用 `--plugin-dir .` 或把本目录加入 Claude Code 的本地插件路径。

> 前提:
> - 在**本项目目录**启动 Claude Code(命令里的 `python -m analyzer.*` 依赖项目环境)。
> - 先 `pip install -r requirements.txt`。
> - 数据走系统代理时,确保 Clash 等已开启可用。

## 边界

所有命令 / 技能都强制「只客观信息、不构成投资建议、不预测涨跌、不替你下单」。方向性研判(会涨 / 见光死 / 该买)涉及证券投顾红线,默认不输出——详见 `PRD.md` §3 合规章节。
