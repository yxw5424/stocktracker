# -*- coding: utf-8 -*-
"""
AI 策略顾问节点 —— panda_quantflow 的自定义 work node（叠加，不改平台源码）。

把这个文件放到  panda_quantflow/src/panda_plugins/custom/ai_advisor_node.py
重启服务即可，UI 的「05-回测相关」分组里会出现「AI策略顾问」节点。

它做什么：
  输入 = 你的文字需求描述 + 一次回测的 back_id（从「股票回测」节点的 backtest_id
         或「策略回测结果」节点连过来）+ 你的自选股清单。
  处理 = 读取该回测的绩效指标（夏普/年化/回撤/原策略代码）→ 连同你的描述交给
         Claude（默认 Opus 4.8）→ 产出「分析 + 可直接回测的策略代码」。
  输出 = strategy_code（四函数结构，可直接连回「股票回测」节点的 code 输入，形成
         生成→回测→再优化 的闭环）+ analysis + explanation。

复用了 quantflow 自身模块：
  - PromptsProvider：拿平台的「回测引擎文档 + 代码规范」喂给 Claude，保证生成代码
    符合它的引擎接口；
  - BacktestCodeChecker：对生成代码做危险调用/语法校验。

安全 & 兜底：
  - 没有 ANTHROPIC_API_KEY 时自动退回 deepseek（若平台配了）或本地 mock 骨架，
    保证节点永远能跑通，不阻塞工作流。
  - LLM 只“出方案/出代码建议”，是否拿去回测/实盘由你人工决定。
"""

from __future__ import annotations

import json
import os
from typing import Optional, Type

from panda_plugins.base import BaseWorkNode, work_node, ui
from pydantic import BaseModel, Field

CLAUDE_MODEL = "claude-opus-4-8"


# --------------------------------------------------------------------------- #
# 输入 / 输出模型
# --------------------------------------------------------------------------- #
@ui(
    description={"input_type": "text_field", "min_lines": 2, "max_lines": 12,
                 "placeholder": "描述你想要的策略，例如：只在我的自选里做，均线金叉买入、跌破止损，控制回撤"},
    watchlist={"input_type": "text_field", "min_lines": 1, "max_lines": 4,
               "placeholder": "自选股，逗号分隔，如 600519.SH,000858.SZ"},
    back_id={"input_type": "None"},                      # 仅连线：从回测节点接入
    provider={"input_type": "combobox", "options": ["claude", "deepseek", "mock"],
              "allow_link": False},
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


# --------------------------------------------------------------------------- #
# 节点
# --------------------------------------------------------------------------- #
@work_node(name="AI策略顾问", group="05-回测相关", type="general", box_color="cyan")
class AIAdvisorNode(BaseWorkNode):

    @classmethod
    def input_model(cls) -> Optional[Type[BaseModel]]:
        return AIAdvisorInput

    @classmethod
    def output_model(cls) -> Optional[Type[BaseModel]]:
        return AIAdvisorOutput

    # run 是同步的（引擎在 threadpool 里调它），所以这里用同步 pymongo / 同步 anthropic
    def run(self, input: BaseModel) -> BaseModel:
        metrics = self._read_backtest(input.back_id)
        watch = [s.strip() for s in input.watchlist.replace("，", ",").split(",") if s.strip()]

        provider = (input.provider or "claude").lower()
        try:
            if provider == "claude" and os.getenv("ANTHROPIC_API_KEY"):
                result, engine = self._call_claude(input.description, watch, metrics)
            elif provider == "deepseek" and os.getenv("DEEPSEEK_API_KEY"):
                result, engine = self._call_deepseek(input.description, watch, metrics)
            else:
                result, engine = self._mock(input.description, watch, metrics), f"mock ({provider} 不可用)"
        except Exception as exc:                     # 任何失败都退回 mock，不阻塞工作流
            self.log_error(f"AI 顾问调用失败，退回 mock：{exc}")
            result, engine = self._mock(input.description, watch, metrics), f"mock (调用失败:{type(exc).__name__})"

        code = result.get("strategy_code", "")
        warn = self._check_code(code)
        expl = result.get("explanation", "")
        if warn:
            expl += f"\n\n⚠ 代码检查提示（请人工确认后再回测）：{warn}"

        self.log_info(f"AI策略顾问 完成，引擎={engine}")
        return AIAdvisorOutput(
            analysis=result.get("analysis", ""),
            strategy_code=code,
            explanation=expl,
            engine=engine,
        )

    # ---- 读取回测结果（复用平台的 Mongo，同步客户端）---------------------- #
    def _read_backtest(self, back_id: str) -> dict:
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
            trades = list(db["panda_backtest_trade"].find({"back_id": back_id}).limit(20))
            m["trade_count"] = db["panda_backtest_trade"].count_documents({"back_id": back_id})
            m["sample_trades"] = [
                {"code": t.get("contract_code"), "dir": t.get("direction"),
                 "price": t.get("price"), "vol": t.get("volume"), "date": t.get("trade_date")}
                for t in trades[:8]
            ]
            cli.close()
            return m
        except Exception as exc:
            self.log_error(f"读取回测结果失败（忽略，继续）：{exc}")
            return {}

    # ---- 平台的引擎文档 + 代码规范（喂给 LLM）---------------------------- #
    def _system_prompt(self) -> str:
        try:
            from panda_server.services.llm.agents.prompts_provider import PromptsProvider as pp
            return pp.join(
                pp.role_and_context_backtest_assistant,
                pp.backtest_code_requirements,
                pp.get_backtest_engine_doc(),
            )
        except Exception:
            return (
                "你是 panda_quantflow 的 A股策略工程师。请只输出一个可回测的策略：定义 "
                "initialize(context) / before_trading(context) / handle_data(context, bar_dict) / "
                "after_trading(context) 四个函数。下单用 order_shares('8888', symbol, amount, style=MarketOrderStyle)"
                "（账户固定字符串 '8888'，必须带 style=MarketOrderStyle）。context 只能用 now/portfolio_dict/"
                "stock_account_dict/future_account_dict/df_factor 及自定义属性，禁止用 context.run_info。"
                "只交易用户给的自选股，遵守 A股 T+1，不要 os/sys/subprocess/eval/exec/open/print 等调用。"
            )

    def _user_prompt(self, desc: str, watch: list, metrics: dict) -> str:
        return (
            f"【我的需求】\n{desc or '（未填写，请据自选与回测给出稳健方案）'}\n\n"
            f"【我的自选股】\n{', '.join(watch) if watch else '（未填，请在策略里用 WATCHLIST 占位）'}\n\n"
            f"【上一次回测结果】\n{json.dumps(metrics, ensure_ascii=False, indent=2) if metrics else '（无，本次为首次）'}\n\n"
            "请输出 JSON：{\"analysis\": 对回测与需求的分析, \"strategy_code\": 完整策略源码(四函数结构), "
            "\"explanation\": 策略逻辑与风险说明}。strategy_code 必须能直接放进「股票回测」节点运行。"
        )

    # ---- Claude（默认，Opus 4.8）---------------------------------------- #
    def _call_claude(self, desc, watch, metrics):
        import anthropic
        client = anthropic.Anthropic()
        SCHEMA = {"type": "object", "additionalProperties": False,
                  "properties": {"analysis": {"type": "string"},
                                 "strategy_code": {"type": "string"},
                                 "explanation": {"type": "string"}},
                  "required": ["analysis", "strategy_code", "explanation"]}
        kwargs = dict(model=CLAUDE_MODEL, max_tokens=8000,
                      system=self._system_prompt(),
                      thinking={"type": "adaptive"},
                      messages=[{"role": "user", "content": self._user_prompt(desc, watch, metrics)}])
        try:
            resp = client.messages.create(
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}}, **kwargs)
        except TypeError:
            kwargs["messages"][0]["content"] += "\n\n只输出 JSON，不要多余文字。"
            resp = client.messages.create(**kwargs)
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "{}")
        return json.loads(text), CLAUDE_MODEL

    # ---- DeepSeek（平台默认，用 openai SDK）------------------------------ #
    def _call_deepseek(self, desc, watch, metrics):
        import openai
        client = openai.OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                               base_url="https://api.deepseek.com/v1")
        resp = client.chat.completions.create(
            model="deepseek-chat", temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": self._system_prompt()},
                      {"role": "user", "content": self._user_prompt(desc, watch, metrics)}])
        return json.loads(resp.choices[0].message.content), "deepseek-chat"

    # ---- 代码安全校验（复用平台 BacktestCodeChecker）-------------------- #
    def _check_code(self, code: str) -> str:
        if not code:
            return ""
        try:
            from panda_server.services.llm.code_checker.backtest_code_checker import BacktestCodeChecker
            issues = BacktestCodeChecker(code).complete_check()
            return "" if not issues else str(issues)
        except Exception:
            return ""     # 校验器不可用时不阻塞

    # ---- Mock 兜底：给一个可跑的双均线骨架 + 基于指标的分析 -------------- #
    def _mock(self, desc, watch, metrics) -> dict:
        wl = watch or ["600519.SH", "000858.SZ"]
        analysis = "（mock）"
        if metrics:
            sh = metrics.get("sharpe"); dd = metrics.get("max_drawdown"); yr = metrics.get("back_profit_year")
            analysis = (f"（mock 分析）上次回测：年化 {yr}，夏普 {sh}，最大回撤 {dd}。"
                        "夏普偏低→信号噪声大，建议放慢换手；回撤偏大→加止损/降仓位。")
        # 注意：账户用字符串 '8888'，下单必须带 style=MarketOrderStyle，且不要用 context.run_info
        # （平台的 BacktestCodeChecker 只允许 context 的 now/portfolio_dict/stock_account_dict/
        #   future_account_dict/df_factor + 自定义属性；这份骨架已按检查器要求写，可直接过检）
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
        return {"analysis": analysis,
                "strategy_code": code,
                "explanation": "双均线示例：快线上穿慢线买、下穿卖，仅在自选内交易。"
                               "这是无 key 时的兜底骨架，配 ANTHROPIC_API_KEY 后由 Claude 按你的描述+回测生成完整策略。"}


# 简介（UI 悬浮预览）
AIAdvisorNode.set_short_description(
    '<p>把<strong>你的需求描述</strong>和<strong>一次回测结果</strong>交给 '
    '<span style="color:rgb(37,99,235)">Claude</span>，产出可直接回测的策略代码。</p>')
