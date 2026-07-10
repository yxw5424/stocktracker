# -*- coding: utf-8 -*-
"""
AI 策略顾问节点 —— panda_quantflow 的自定义 work node（叠加，不改平台源码）。

放到  panda_quantflow/src/panda_plugins/custom/ai_advisor_node.py  重启即可，
UI 的「05-回测相关」分组里会出现「AI策略顾问」节点。

  输入 = 文字需求描述 + 一次回测的 back_id（从「股票回测」的 backtest_id 连过来）+ 自选股清单
  处理 = 读该回测绩效 → 交给 Claude(默认 Opus 4.8) → 用平台 PromptsProvider 约束、
         BacktestCodeChecker 校验
  输出 = strategy_code(四函数结构，可连回「股票回测」的 code) + analysis + explanation

本文件同时导出模块级函数（call_llm / read_backtest / check_code），供
ai_strategy_loop_node.py 复用（两文件请一起放进 custom/）。

安全：无 ANTHROPIC_API_KEY 时自动退回 deepseek 或本地 mock 骨架；LLM 只出建议，不自动实盘。
"""

from __future__ import annotations

import json
import os
from typing import Optional, Type

from panda_plugins.base import BaseWorkNode, work_node, ui
from pydantic import BaseModel, Field

CLAUDE_MODEL = "claude-opus-4-8"


# =========================================================================== #
# 模块级工具函数（节点 + 循环节点共用）
# =========================================================================== #
def read_backtest(back_id: str, log=None) -> dict:
    """用平台同一个 Mongo（同步客户端）读一次回测的绩效指标 + 少量成交。失败返回 {}。"""
    if not back_id:
        return {}
    try:
        from bson import ObjectId
        from pymongo import MongoClient
        uri = os.getenv("MONGO_URI", "localhost:27017")
        kwargs = {"serverSelectionTimeoutMS": 3000}
        user, pwd = os.getenv("MONGO_USER", "panda"), os.getenv("MONGO_PASSWORD", "panda")
        if user and pwd:
            kwargs.update(username=user, password=pwd,
                          authSource=os.getenv("MONGO_AUTH_DB", "admin"))
        if os.getenv("MONGO_TYPE", "replica_set") == "replica_set":
            kwargs["replicaSet"] = os.getenv("MONGO_REPLICA_SET", "rs0")
        cli = MongoClient(uri if uri.startswith("mongodb") else f"mongodb://{uri}", **kwargs)
        db = cli[os.getenv("DATABASE_NAME", "panda")]
        doc = db["panda_back_test"].find_one({"_id": ObjectId(back_id)}) or {}
        keys = ["back_profit", "back_profit_year", "sharpe", "sortino", "max_drawdown",
                "alpha", "beta", "volatility", "information_ratio", "strategy_code",
                "start_date", "end_date", "run_status"]
        m = {k: doc.get(k) for k in keys if k in doc}
        m["trade_count"] = db["panda_backtest_trade"].count_documents({"back_id": back_id})
        cli.close()
        return m
    except Exception as exc:
        if log:
            log(f"读取回测结果失败（忽略）：{exc}")
        return {}


# 硬性 API 白名单 —— 逐条对照过引擎源码(bar_map/stock_api/stock_account 等)。
# 目的：堵死 LLM 发明不存在函数(如 stock_api_daily)与不防停牌两类高频错误。
HARD_RULES = """
【硬性约束 —— 违反任何一条要么报错、要么静默 0 成交，逐条自查后再输出】
1. 开头的 import 必须完整。可用 numpy/pandas/datetime，但【用什么就必须 import 什么】——
   常见事故：用了 np. 却没 import numpy → NameError 崩溃。固定用下面这组开头，不要省：
   from panda_backtest.api.api import *
   from panda_backtest.api.stock_api import *
   import numpy as np
   import pandas as pd
   import datetime

2. 【关键】历史数据一律在 context 里自建缓存，禁止用 stock_api_quotation 取历史。
   原因：该接口 period 必须精确等于 '1d'、fields 不含 'symbol' 时返回表没有 symbol 列、
   空结果是空表——任何一个没处理好都会被下面的 try/except 吞掉，导致「一笔不交易、收益0%」。
   正确做法：在 handle_data 里每天把 bar[s].close 追加进 context.hist[s] 列表，从列表算均线/动量。

3. 你能用的接口只有这些，签名一字不差：
   - bar[symbol]：当前bar，symbol 形如 '562500.SH'。停牌返回 None，必须判空：
         b = bar[s]
         if b is None or getattr(b,'close',None) in (None,0): continue
     可用字段：b.open/b.high/b.low/b.close/b.volume。
   - order_shares('8888', symbol, 股数, style=MarketOrderStyle)：正数买、负数卖，股数为100整数倍。
   - context.trade_date：当前回测日(int或str,如20240102)。星期几：
     import datetime; datetime.datetime.strptime(str(context.trade_date),'%Y%m%d').weekday()  # 4=周五
   - acc = context.stock_account_dict['8888']：acc.cash 可用资金 / acc.total_value 总资产 /
     acc.positions 持仓字典。遍历：for sym,pos in acc.positions.items():，pos.sellable 可卖数量。
   - SRLogger.info('...') 打日志（禁止 print）。

4. 【必须】每次触发买/卖后 SRLogger.info 记一行(含日期/标的/动作/价格)；
   若某天想买但被资金/涨停/仓位挡住，也要 SRLogger.info 说明原因。
   这样"0成交"时你能从日志看到到底卡在哪，而不是一片空白。

5. 单只股票的处理用 try/except，但 except 里必须 SRLogger.info 记下异常，禁止裸 continue 吞错。

6. 下列名字都不存在，出现即错：stock_api_daily/get_history/history_bars/get_price/
   attribute_history/order_target/order_percent/context.run_info。
   禁止模块级全局变量、禁止 os/sys/subprocess/eval/exec/open。

7. 必须照抄下面这个【可运行参考骨架】的数据缓存与下单结构，再改信号逻辑：
```python
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

def initialize(context):
    context.universe = ['562500.SH','512760.SH']   # 用户自选，逗号分隔填进来
    context.hist = {s: [] for s in context.universe}
    context.hold = set()

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    prices = {}
    for s in context.universe:
        try:
            b = bar[s]
            if b is None or getattr(b,'close',None) in (None,0):
                continue
            prices[s] = b.close
            context.hist[s].append(b.close)
            if len(context.hist[s]) > 250:
                context.hist[s] = context.hist[s][-250:]
        except Exception as e:
            SRLogger.info('数据异常 %s: %s' % (s, e)); continue

    for s in list(context.hold):                       # 先处理卖出
        h = context.hist.get(s, [])
        if s in prices and len(h) >= 20:
            ma20 = sum(h[-20:]) / 20
            if prices[s] < ma20:                       # 跌破20日线卖出
                pos = acc.positions.get(s)
                if pos and pos.sellable > 0:
                    order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                    context.hold.discard(s)
                    SRLogger.info('卖出 %s @ %.3f' % (s, prices[s]))

    for s in context.universe:                         # 再处理买入
        if s in context.hold or s not in prices:
            continue
        h = context.hist.get(s, [])
        if len(h) < 60:
            continue
        ma20, ma60 = sum(h[-20:])/20, sum(h[-60:])/60
        if ma20 > ma60 and len(context.hold) < 4:      # 金叉且持仓未满
            budget = acc.total_value * 0.24
            qty = int(budget / prices[s] / 100) * 100
            if qty >= 100 and acc.cash >= qty * prices[s]:
                order_shares('8888', s, qty, style=MarketOrderStyle)
                context.hold.add(s)
                SRLogger.info('买入 %s %d股 @ %.3f' % (s, qty, prices[s]))
            else:
                SRLogger.info('想买 %s 但资金不足或不足100股' % s)
```
这个骨架能真实成交。你的任务是把里面的信号逻辑换成用户要的策略，数据缓存/下单/日志结构保持不变。
"""


def _log_code(log, code: str, tag: str = "生成的策略代码"):
    """把策略代码分块打进运行日志(UI 看不到节点输出时,可从日志复制)。"""
    if not code:
        log(f"⚠ {tag}为空(LLM 未产出代码)")
        return
    lines = code.splitlines()
    log(f"===== {tag}({len(lines)} 行,可从下方复制)=====")
    for i in range(0, len(lines), 25):
        log("\n".join(lines[i:i + 25]))
    log("===== 代码结束 =====")


def system_prompt() -> str:
    """优先复用平台的「回测引擎文档 + 代码规范」，再叠加硬性 API 白名单。"""
    try:
        from panda_server.services.llm.agents.prompts_provider import PromptsProvider as pp
        base = pp.join(
            pp.role_and_context_backtest_assistant,
            pp.backtest_code_requirements,
            pp.get_backtest_engine_doc(),
        )
    except Exception:
        base = (
            "你是 panda_quantflow 的 A股策略工程师。只输出一个可回测的策略：定义 "
            "initialize(context) / before_trading(context) / handle_data(context, bar_dict) / "
            "after_trading(context) 四个函数。只交易用户给的自选股，遵守 A股 T+1。"
        )
    return base + "\n" + HARD_RULES


def user_prompt(desc: str, watch: list, metrics: dict, prev_code: str = "") -> str:
    parts = [
        f"【我的需求】\n{desc or '（未填写，请据自选与回测给出稳健方案）'}",
        f"【我的自选股】\n{', '.join(watch) if watch else '（未填，请用 WATCHLIST 占位）'}",
    ]
    if prev_code:
        parts.append(f"【上一轮策略代码】\n```python\n{prev_code}\n```")
    parts.append(f"【上一次回测结果】\n{json.dumps(metrics, ensure_ascii=False, indent=2) if metrics else '（无，本次为首次）'}")
    if metrics:
        parts.append("请针对上面的回测结果改进策略（例如夏普低→降换手/加过滤，回撤大→止损/降仓）。")
    parts.append("输出 JSON：{\"analysis\": 对回测与需求的分析, \"strategy_code\": 完整策略源码(四函数), "
                 "\"explanation\": 逻辑与风险说明}。strategy_code 必须能直接放进「股票回测」节点。")
    return "\n\n".join(parts)


def check_code(code: str) -> str:
    """复用平台 BacktestCodeChecker 校验；返回问题串（空=OK）。校验器不可用则返回空不阻塞。"""
    if not code:
        return ""
    try:
        from panda_server.services.llm.code_checker.backtest_code_checker import BacktestCodeChecker
        issues = BacktestCodeChecker(code).complete_check()
        return "" if not issues else str(issues)
    except Exception:
        return ""


def call_llm(provider: str, desc: str, watch: list, metrics: dict, prev_code: str = "", log=None):
    """统一 LLM 入口：返回 (result_dict, engine_str)。任何失败退回 mock，不抛。"""
    provider = (provider or "claude").lower()
    try:
        if provider == "claude" and os.getenv("ANTHROPIC_API_KEY"):
            return _call_claude(desc, watch, metrics, prev_code), CLAUDE_MODEL
        if provider == "deepseek" and os.getenv("DEEPSEEK_API_KEY"):
            return _call_deepseek(desc, watch, metrics, prev_code), "deepseek-chat"
        return _mock(desc, watch, metrics), f"mock ({provider} 不可用)"
    except Exception as exc:
        if log:
            log(f"LLM 调用失败，退回 mock：{exc}")
        return _mock(desc, watch, metrics), f"mock (调用失败:{type(exc).__name__})"


def _call_claude(desc, watch, metrics, prev_code=""):
    import anthropic
    client = anthropic.Anthropic()
    SCHEMA = {"type": "object", "additionalProperties": False,
              "properties": {"analysis": {"type": "string"},
                             "strategy_code": {"type": "string"},
                             "explanation": {"type": "string"}},
              "required": ["analysis", "strategy_code", "explanation"]}
    kwargs = dict(model=CLAUDE_MODEL, max_tokens=8000, system=system_prompt(),
                  thinking={"type": "adaptive"},
                  messages=[{"role": "user", "content": user_prompt(desc, watch, metrics, prev_code)}])
    try:
        resp = client.messages.create(
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}}, **kwargs)
    except TypeError:
        kwargs["messages"][0]["content"] += "\n\n只输出 JSON，不要多余文字。"
        resp = client.messages.create(**kwargs)
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "{}")
    return json.loads(text)


def _call_deepseek(desc, watch, metrics, prev_code=""):
    import openai
    client = openai.OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                           base_url="https://api.deepseek.com/v1")
    resp = client.chat.completions.create(
        model="deepseek-chat", temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system_prompt()},
                  {"role": "user", "content": user_prompt(desc, watch, metrics, prev_code)}])
    return json.loads(resp.choices[0].message.content)


def _mock(desc, watch, metrics) -> dict:
    wl = watch or ["600519.SH", "000858.SZ"]
    analysis = "（mock）以量化信号为主、控制单票与换手。"
    if metrics:
        analysis = (f"（mock 分析）上次回测：年化 {metrics.get('back_profit_year')}，"
                    f"夏普 {metrics.get('sharpe')}，最大回撤 {metrics.get('max_drawdown')}。"
                    "夏普偏低→放慢换手；回撤偏大→加止损/降仓位。")
    # 账户用 '8888'，下单带 style=MarketOrderStyle，不用 context.run_info —— 已过平台 BacktestCodeChecker
    code = (
        "# 双均线示例（mock 兜底骨架，已通过平台 BacktestCodeChecker）：先小仓验证\n"
        "from panda_backtest.api.api import *\n"
        "from panda_backtest.api.stock_api import *\n\n"
        f"WATCHLIST = {wl!r}\n"
        "ACCOUNT = '8888'\n"
        "FAST, SLOW = 5, 20\n\n"
        "def initialize(context):\n"
        "    context.watch = WATCHLIST\n"
        "    context.px = {s: [] for s in WATCHLIST}\n\n"
        "def before_trading(context):\n"
        "    pass\n\n"
        "def handle_data(context, bar_dict):\n"
        "    for s in context.watch:\n"
        "        bar = bar_dict.get(s)\n"
        "        if bar is None:\n"
        "            continue\n"
        "        seq = context.px[s]\n"
        "        seq.append(bar.close)\n"
        "        if len(seq) < SLOW:\n"
        "            continue\n"
        "        fast = sum(seq[-FAST:]) / FAST\n"
        "        slow = sum(seq[-SLOW:]) / SLOW\n"
        "        if fast > slow:\n"
        "            order_shares(ACCOUNT, s, 100, style=MarketOrderStyle)\n"
        "        elif fast < slow:\n"
        "            order_shares(ACCOUNT, s, -100, style=MarketOrderStyle)\n\n"
        "def after_trading(context):\n"
        "    pass\n"
    )
    return {"analysis": analysis, "strategy_code": code,
            "explanation": "双均线示例：快线上穿慢线买、下穿卖，仅在自选内交易。"
                           "无 key 时的兜底骨架；配 ANTHROPIC_API_KEY 后由 Claude 按描述+回测生成完整策略。"}


def parse_watchlist(text: str) -> list:
    return [s.strip() for s in (text or "").replace("，", ",").split(",") if s.strip()]


# =========================================================================== #
# 节点
# =========================================================================== #
@ui(
    description={"input_type": "text_field", "min_lines": 2, "max_lines": 12,
                 "placeholder": "描述你想要的策略，例如：只在我的自选里做，均线金叉买入、跌破止损，控制回撤"},
    watchlist={"input_type": "text_field", "min_lines": 1, "max_lines": 4,
               "placeholder": "自选股，逗号分隔，如 600519.SH,000858.SZ"},
    back_id={"input_type": "None"},
    provider={"input_type": "combobox", "options": ["claude", "deepseek", "mock"], "allow_link": False},
)
class AIAdvisorInput(BaseModel):
    description: str = Field(default="", title="策略需求描述")
    watchlist: str = Field(default="", title="自选股清单")
    back_id: str = Field(default="", title="回测ID")
    provider: str = Field(default="claude", title="LLM 提供方")


class AIAdvisorOutput(BaseModel):
    analysis: str = Field(default="", title="回测/需求分析")
    strategy_code: str = Field(default="", title="生成的策略代码")
    explanation: str = Field(default="", title="策略说明")
    engine: str = Field(default="", title="实际使用的模型")


@work_node(name="AI策略顾问", group="05-回测相关", type="general", box_color="cyan")
class AIAdvisorNode(BaseWorkNode):

    @classmethod
    def input_model(cls) -> Optional[Type[BaseModel]]:
        return AIAdvisorInput

    @classmethod
    def output_model(cls) -> Optional[Type[BaseModel]]:
        return AIAdvisorOutput

    def run(self, input: BaseModel) -> BaseModel:
        metrics = read_backtest(input.back_id, log=self.log_error)
        watch = parse_watchlist(input.watchlist)
        result, engine = call_llm(input.provider, input.description, watch, metrics, log=self.log_error)

        code = result.get("strategy_code", "")
        expl = result.get("explanation", "")
        warn = check_code(code)
        if warn:
            expl += f"\n\n⚠ 代码检查提示（请人工确认后再回测）：{warn}"

        self.log_info(f"AI策略顾问 完成，引擎={engine}")
        _log_code(self.log_info, code)   # 把生成的策略代码打进运行日志,方便查看/复制
        return AIAdvisorOutput(analysis=result.get("analysis", ""),
                               strategy_code=code, explanation=expl, engine=engine)


AIAdvisorNode.set_short_description(
    '<p>把<strong>你的需求描述</strong>和<strong>一次回测结果</strong>交给 '
    '<span style="color:rgb(37,99,235)">Claude</span>，产出可直接回测的策略代码。</p>')
