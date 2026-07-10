# -*- coding: utf-8 -*-
"""
AI 策略自迭代节点 —— 在一个节点里跑「生成→回测→读回测→再改」多轮闭环。

放到  panda_quantflow/src/panda_plugins/custom/ai_strategy_loop_node.py
（需与 ai_advisor_node.py 一起放进 custom/，本节点复用其 LLM/校验/读回测函数）

流程：
  第1轮：按你的描述让 Claude 生成策略 → 校验 → 用平台回测引擎跑 → 读绩效
  第2..N轮：把上一轮的代码+绩效回喂 Claude，让它改进 → 再跑 → 再读
  结束：达到 max_rounds，或达到目标指标（如 夏普≥1.5）时提前停
  输出：最优的一版策略代码 + 其绩效 + 每一轮的历史

复用平台：panda_backtest.main_workflow_stock.start（同「股票回测」节点）、
          BacktestCodeChecker、PromptsProvider（经 ai_advisor_node）。
安全：只跑回测、产出建议，绝不自动实盘。
"""

from __future__ import annotations

import json
from typing import Optional, Type

from panda_plugins.base import BaseWorkNode, work_node, ui
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 纯逻辑：依赖注入，便于单测（generate/backtest/read_metrics 均可替换）
# --------------------------------------------------------------------------- #
def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_strategy_loop(*, description, watch, max_rounds, target_metric, target_value,
                      generate, backtest, read_metrics, check=None, log=None):
    """跑多轮自迭代，返回 {best_code, best_metrics, best_round, rounds:[...]}。

    generate(description, watch, prev_metrics, prev_code) -> {strategy_code, explanation, analysis}
    backtest(code) -> back_id（字符串）；失败可抛异常
    read_metrics(back_id) -> dict（含 sharpe / back_profit_year / max_drawdown ...）
    check(code) -> "" 表示通过，否则返回问题串（可为 None 表示不校验）
    目标指标一律「越大越好」（max_drawdown 存为负数，越大=回撤越浅）。
    """
    history, best = [], None
    prev_code, prev_metrics = "", {}
    last_valid_code, last_valid_round = "", 0   # 最后一版通过静态校验的代码(兜底用)

    for rnd in range(1, int(max_rounds) + 1):
        if log:
            log(f"[自迭代] 第 {rnd}/{max_rounds} 轮：生成策略中…")
        gen = generate(description, watch, prev_metrics, prev_code) or {}
        code = gen.get("strategy_code", "")
        rec = {"round": rnd, "explanation": gen.get("explanation", "")}

        if not code:
            rec["error"] = "LLM 未产出代码"
            history.append(rec)
            continue

        # 静态校验（复用平台检查器）
        warn = check(code) if check else ""
        if warn:
            rec["error"] = f"代码检查未过：{warn}"
            history.append(rec)
            prev_code, prev_metrics = code, {"code_check_error": warn}   # 回喂错误让下一轮修
            if log:
                log(f"[自迭代] 第 {rnd} 轮代码检查未过，回喂修复")
            continue

        last_valid_code, last_valid_round = code, rnd

        # 回测
        try:
            back_id = str(backtest(code))
        except Exception as exc:
            rec["error"] = f"回测异常：{str(exc)[:200]}"
            history.append(rec)
            prev_code, prev_metrics = code, {"backtest_error": str(exc)[:200]}
            continue

        # 读绩效
        metrics = read_metrics(back_id) or {}
        rec.update({"backtest_id": back_id, "metrics": metrics})
        history.append(rec)

        # 记录最优
        score = _to_float(metrics.get(target_metric))
        best_score = _to_float((best or {}).get("metrics", {}).get(target_metric))
        if score is not None and (best is None or best_score is None or score > best_score):
            best = {"code": code, "metrics": metrics, "round": rnd, "explanation": gen.get("explanation", "")}

        prev_code, prev_metrics = code, metrics

        # 达标提前停
        tgt = _to_float(target_value)
        if tgt is not None and score is not None and score >= tgt:
            if log:
                log(f"[自迭代] 第 {rnd} 轮达到目标 {target_metric}={score} ≥ {tgt}，提前结束")
            break

    # 兜底：所有轮次都没取得目标指标(如回测环境异常)时，输出最后一版通过
    # 校验的代码，绝不输出空代码（空代码连到回测节点会报"缺少必要方法：initialize"）。
    if best is None and last_valid_code:
        if log:
            log(f"[自迭代] 各轮均未取得 {target_metric} 指标(多为回测环境/数据问题)，"
                f"best_code 采用第 {last_valid_round} 轮通过校验的代码兜底")
        best = {"code": last_valid_code, "metrics": {}, "round": last_valid_round,
                "explanation": "各轮回测未产出指标，此为最后一版通过校验的代码(兜底)"}

    return {
        "best_code": (best or {}).get("code", ""),
        "best_metrics": (best or {}).get("metrics", {}),
        "best_round": (best or {}).get("round", 0),
        "best_explanation": (best or {}).get("explanation", ""),
        "rounds": history,
    }


# --------------------------------------------------------------------------- #
# 节点
# --------------------------------------------------------------------------- #
@ui(
    description={"input_type": "text_field", "min_lines": 2, "max_lines": 12,
                 "placeholder": "描述你想要的策略；AI 会自动多轮改进"},
    watchlist={"input_type": "text_field", "min_lines": 1, "max_lines": 4,
               "placeholder": "自选股，逗号分隔"},
    max_rounds={"input_type": "number_field", "placeholder": "最大迭代轮数", "allow_link": False},
    target_metric={"input_type": "combobox",
                   "options": ["sharpe", "back_profit_year", "max_drawdown"], "allow_link": False},
    target_value={"input_type": "number_field", "placeholder": "达标即停(留空=跑满轮数)", "allow_link": False},
    provider={"input_type": "combobox", "options": ["claude", "deepseek", "mock"], "allow_link": False},
    start_date={"input_type": "date_field", "allow_link": False},
    end_date={"input_type": "date_field", "allow_link": False},
    start_capital={"input_type": "number_field", "allow_link": False},
    standard_symbol={"input_type": "combobox", "options": ["上证指数", "沪深300", "中证500", "中证1000"]},
    commission_rate={"input_type": "number_field", "allow_link": False},
)
class LoopInput(BaseModel):
    description: str = Field(default="", title="策略需求描述")
    watchlist: str = Field(default="", title="自选股清单")
    max_rounds: int = Field(default=3, title="最大迭代轮数")
    target_metric: str = Field(default="sharpe", title="目标指标")
    target_value: float = Field(default=0.0, title="目标值(0=不设，跑满轮数)")
    provider: str = Field(default="claude", title="LLM 提供方")
    start_date: str = Field(default="20241001", title="回测开始")
    end_date: str = Field(default="20241231", title="回测结束")
    start_capital: int = Field(default=10000000, title="初始资金")
    standard_symbol: str = Field(default="上证指数", title="基准指数")
    commission_rate: int = Field(default=1, title="佣金率")


class LoopOutput(BaseModel):
    best_code: str = Field(default="", title="最优策略代码")
    best_round: int = Field(default=0, title="最优出现在第几轮")
    best_metrics: str = Field(default="", title="最优绩效(JSON)")
    rounds: str = Field(default="", title="各轮历史(JSON)")
    summary: str = Field(default="", title="小结")


@work_node(name="AI策略自迭代", group="05-回测相关", type="general", box_color="cyan")
class AIStrategyLoopNode(BaseWorkNode):

    @classmethod
    def input_model(cls) -> Optional[Type[BaseModel]]:
        return LoopInput

    @classmethod
    def output_model(cls) -> Optional[Type[BaseModel]]:
        return LoopOutput

    def run(self, input: BaseModel) -> BaseModel:
        # 复用顾问节点的 LLM / 校验 / 读回测
        from panda_plugins.custom.ai_advisor_node import call_llm, read_backtest, check_code, parse_watchlist
        from panda_backtest.main_workflow_stock import start, get_backtest_id
        import pandas as pd

        watch = parse_watchlist(input.watchlist)

        def _generate(desc, wl, prev_metrics, prev_code):
            result, engine = call_llm(input.provider, desc, wl, prev_metrics, prev_code, log=self.log_error)
            return result

        def _backtest(code):
            bid = get_backtest_id()
            start(back_test_id=bid, code=code,
                  start_date=input.start_date, end_date=input.end_date, frequency="1d",
                  start_capital=input.start_capital, standard_symbol=input.standard_symbol,
                  commission_rate=input.commission_rate, account_id="8888",
                  df_factor=pd.DataFrame())
            return bid

        out = run_strategy_loop(
            description=input.description, watch=watch,
            max_rounds=input.max_rounds, target_metric=input.target_metric,
            target_value=(input.target_value or None),
            generate=_generate, backtest=_backtest, read_metrics=read_backtest,
            check=check_code, log=self.log_info,
        )

        done = len(out["rounds"])
        summary = (f"共 {done} 轮；最优出现在第 {out['best_round']} 轮，"
                   f"{input.target_metric}={out['best_metrics'].get(input.target_metric)}。"
                   "最优代码已在 best_code，可连回「股票回测」复跑确认。")
        self.log_info(summary)
        try:
            from ai_advisor_node import _log_code
            _log_code(self.log_info, out["best_code"], tag="最优策略代码(best_code)")
        except Exception:
            pass
        return LoopOutput(
            best_code=out["best_code"], best_round=out["best_round"],
            best_metrics=json.dumps(out["best_metrics"], ensure_ascii=False),
            rounds=json.dumps(out["rounds"], ensure_ascii=False),
            summary=summary,
        )


AIStrategyLoopNode.set_short_description(
    '<p>一个节点跑完 <strong>生成→回测→读回测→再改</strong> 多轮闭环，输出最优策略。</p>')
