# 📈 stcoktracker — A股实时异动监控 + 自适应汇报 + GitHub 网站

一个自循环的盯盘 agent:定时抓取目标股票分钟行情,计算**斜率/动量**,
**斜率激增时自动切到高频节奏**,满足触发条件就**推送告警**,并把结果发布到一个
**GitHub Pages 网站看板**。

> ⚠️ 信息提醒工具,**不构成投资建议,不自动下单**。A股免费数据有延迟/限频,精确价以券商为准。

---

## 它怎么工作

```
[定时触发] → analyzer.run
   ├─ fetch.py     akshare 抓分钟K线(--demo 用合成数据)
   ├─ analyze.py   算 涨跌幅 / 斜率 / 斜率加速度 / 量比 / 突破
   ├─ rules.py     提示词规则引擎:封闭词表硬指标 DSL 确定性命中 + 按规则冷却/每日上限 + 影子模式
   ├─ state.py     自适应节奏(斜率激增→高频)+ 告警去重
   ├─ notify.py    推送(PushPlus/Server酱/Bark/Telegram/邮件,按需开)
   └─ 写 docs/data/*.json
          ↓
   [GitHub Pages 看板 docs/]  ←每60秒自动刷新
```

**自适应节奏**:正常每 `base_interval_minutes`(默认60分)最多汇报一次;一旦触发
`slope_surge`,进入高频模式,改为每 `fast_interval_minutes`(默认15分)汇报,
持续 `fast_mode_cooldown_minutes`(默认45分)后自动降回。全部在 `config.yaml` 里调。

---

## 快速开始(本地)

```bash
pip install -r requirements.txt

# 离线演示:用合成数据跑通流水线(末段故意造了斜率激增+放量,能看到高频模式)
python -m analyzer.run --demo

# 本地预览网站
python -m http.server -d docs 8000      # 浏览器开 http://localhost:8000

# 真·动态间隔的常驻运行(斜率激增就缩短 sleep)
python scripts/run_local.py             # 联网,仅交易时段取数
python scripts/run_local.py --demo      # 离线演示
```

---

## 发布到 GitHub(云端定时 + 网站)

1. 新建一个 GitHub 仓库,把本目录推上去:
   ```bash
   git init && git add -A && git commit -m "init: stock monitor"
   git branch -M main
   git remote add origin https://github.com/<你>/<仓库>.git
   git push -u origin main
   ```
2. **开启 Pages**:仓库 → Settings → Pages → Source 选 **Deploy from a branch** →
   分支 `main`、目录 `/docs` → Save。几分钟后得到网址 `https://<你>.github.io/<仓库>/`。
3. **定时任务**:`.github/workflows/monitor.yml` 已配好(交易时段每15分钟)。
   首次推送后在 **Actions** 页可手动 `Run workflow` 测一次。
4. **配置推送渠道(可选)**:仓库 → Settings → Secrets and variables → Actions →
   New repository secret,按需添加(没配的渠道自动跳过):

   | 渠道 | 需要的 Secret |
   |---|---|
   | PushPlus(微信) | `PUSHPLUS_TOKEN` |
   | Server酱(微信) | `SERVERCHAN_KEY` |
   | Bark(iOS) | `BARK_URL` |
   | Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
   | 邮件 | `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `SMTP_TO` |

---

## 提示词规则引擎(`rules.yaml`)

用大白话写盯盘条件,引擎**确定性**地判断是否命中(无 LLM、毫秒级、可复算)——这是 PRD 的 P0 核心:
"把聪明放配置期,把确定放运行期"。

```bash
python -m analyzer.rules                                   # 列出已配置规则(含阈值)
python -m analyzer.rules --replay r_volbreak --code 600909 # 历史回放该规则(PIT 对齐)
python -m analyzer.rules --replay r_surge --code 600909 --demo  # 离线合成日线回放
```

- **封闭词表硬指标**:`slope`(斜率)`accel` `vol_ratio`(量比)`pct_window` `pct_day` `gap`(跳空)
  `amplitude`(振幅)`price_breakout`(突破箱体)`intraday_shape`(高开低走 / 加速 / 跳水)。
- **降噪一等公民**:每条规则自带 `cooldown_sec` 冷却 + `max_alerts_per_day` 每日上限。
- **影子模式**(`shadow: true`):只记录不推送,先观察 1~2 天再放量;看板会显示影子命中。
- **历史回放(PIT 对齐)**:每天只用当天之前的数据算特征,防前视偏差;`slope/分时形态` 等分时级条件
  日线无法复现,会被**诚实标注并忽略**。
- **铁律**:只报客观事实(放量 X 倍、突破近 N 日箱体、高开低走),**绝不输出方向性结论**(会涨/见光死/该买)。
- 配规则最省心的方式:在 Claude Code 里用 `/rule "你的大白话"`,自动拆解 + 回译 + 落地;`/rules` 查看/回放。

## 改配置(`config.yaml`)

- `targets`:加减监控的股票;`levels` 填关键价位线即可启用突破告警。
- `triggers`:四类内置触发器(涨跌幅 / 斜率激增 / 放量 / 突破)各自开关与阈值。
- `cadence`:正常/高频间隔、高频持续时长、告警去重窗口。
- `analysis.bar_period`:分钟周期(1/5/15/30/60)。
- 自定义规则放 `rules.yaml`(见上),与内置触发器并行。

---

## 已知限制(请知悉)

- **GitHub Actions 定时是尽力而为**,可能延迟几分钟;cron 最小 5 分钟;
  **仓库连续 60 天无提交,定时会被自动暂停**(本项目每次跑都会 commit 数据,通常不会触发)。
- **akshare 是非官方接口**,会限频/因源站改版失效;不是逐笔实时。
- **只在交易时段有数据**;休市时沿用上一次快照。
- 突破告警为简化实现(基于当前价与价位线比较 + 去重),不是严格的"上一根→这一根穿越"。

---

## 目录

```
config.yaml                 监控配置(标的/阈值/节奏/渠道)
rules.yaml                  提示词规则(硬指标 DSL,用 /rule 维护)
requirements.txt
analyzer/                   分析与推送核心
  fetch.py  analyze.py  rules.py  state.py  notify.py  run.py
  market.py  signals.py  digest.py  screen.py  backtest.py  providers.py
docs/                       GitHub Pages 网站(看板)
  index.html  app.js  style.css  data/(运行时生成的 json)
scripts/run_local.py        本地常驻(真·动态间隔)
.github/workflows/monitor.yml  云端定时任务
```
