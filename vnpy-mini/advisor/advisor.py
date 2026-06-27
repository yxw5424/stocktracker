"""
AI 投资顾问 —— 把日频上下文交给 Claude，产出"下一步是否操作、如何操作"的结构化决策。

设计原则（安全第一）：
  * 顾问只**给建议**，由人确认后才下单；任何情况下不自动实盘。
  * 量化多因子调仓 与 波段（半主观）想法 分开输出。
  * 必须给出风险提示与信心度；A股要考虑 T+1、涨跌停、流动性。

模型：默认 Claude Opus 4.8（claude-opus-4-8），自适应思考 + 结构化输出。
无 ANTHROPIC_API_KEY 或未装 anthropic 时，自动退回一个确定性的 mock 顾问，
让整条链路仍可演示。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

MODEL = "claude-opus-4-8"

SYSTEM = """你是一名稳健的 A股投资顾问，同时是量化研究助手。你的任务是基于当天给到的
上下文（账户与持仓、量化多因子选股信号、大盘表现、当日新闻、个股近期走势），判断
"下一步是否要操作、如何操作"，并动态调整策略。

必须遵守：
1. 你只给**建议**，由用户人工确认后才会下单——绝不假设会自动成交。
2. 区分两类操作：
   - 量化调仓：基于多因子信号的组合再平衡（给目标权重）。
   - 波段想法：半主观的短线机会（给入场、止损、止盈区间与逻辑）。
3. A股现实：T+1（当日买入次日才能卖）、涨跌停可能无法成交、注意流动性与停牌。
4. 永远给出风险提示与信心度；信息不足时宁可保守（多用 hold / reduce）。
5. 不做夸大承诺，不保证收益。仓位建议要克制，单一标的不过度集中。
严格按给定 JSON schema 输出。"""

# 决策的结构化输出 schema
DECISION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "market_view": {"type": "string", "description": "对当日/次日大盘的简要看法"},
        "stance": {"type": "string", "enum": ["risk_on", "neutral", "risk_off"]},
        "actions": {
            "type": "array",
            "description": "量化调仓建议",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["buy", "add", "hold", "reduce", "sell"]},
                    "target_weight": {"type": "number"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["symbol", "action", "target_weight", "reason", "confidence"],
            },
        },
        "swing_ideas": {
            "type": "array",
            "description": "波段（半主观）想法",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "symbol": {"type": "string"},
                    "setup": {"type": "string"},
                    "entry": {"type": "string"},
                    "stop": {"type": "string"},
                    "take_profit": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "setup", "entry", "stop", "take_profit", "reason"],
            },
        },
        "risk_notes": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["market_view", "stance", "actions", "swing_ideas", "risk_notes", "confidence"],
}


def _user_message(context: Dict[str, Any]) -> str:
    sess = "开盘前（决定今日是否操作）" if context.get("session") == "open" else "收盘后（复盘并规划下一交易日）"
    return (
        f"现在是 {context.get('date')} 的【{sess}】。请根据以下上下文给出决策。\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n\n"
        "请输出符合 schema 的 JSON：先给大盘看法与整体风险姿态，再给量化调仓建议"
        "（actions，目标权重之和建议≤1）与波段想法（swing_ideas），最后给风险提示与信心度。"
    )


def run_advisor(
    context: Dict[str, Any],
    *,
    model: str = MODEL,
    api_key: Optional[str] = None,
    max_tokens: int = 4000,
) -> Dict[str, Any]:
    """调用 Claude 产出决策；无 key/SDK 时退回 mock。返回 dict（含 _engine 标记来源）。"""
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    try:
        import anthropic
    except Exception:
        anthropic = None

    if not key or anthropic is None:
        out = mock_advisor(context)
        out["_engine"] = "mock"
        return out

    try:
        client = anthropic.Anthropic(api_key=key)
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": _user_message(context)}],
        )
        try:
            resp = client.messages.create(
                output_config={"format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
                **kwargs,
            )
        except TypeError:
            # 老版本 SDK 不认 output_config：退回纯文本 + 提示输出 JSON
            kwargs["messages"][0]["content"] += "\n\n只输出 JSON，不要任何额外文字。"
            resp = client.messages.create(**kwargs)

        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        decision = json.loads(text)
        decision["_engine"] = model
        return decision
    except Exception as exc:
        out = mock_advisor(context)
        out["_engine"] = f"mock (Claude 调用失败: {type(exc).__name__})"
        return out


# --------------------------------------------------------------------------- #
# Mock 顾问：无 key 时的确定性兜底，让链路可演示。
# 规则：跟随量化信号建仓 top 名单；持仓中不在信号里的减仓；大盘缺失则中性。
# --------------------------------------------------------------------------- #
def mock_advisor(context: Dict[str, Any]) -> Dict[str, Any]:
    signals = context.get("quant_signals", []) or []
    positions = {p.get("symbol"): p for p in (context.get("positions", []) or [])}
    actions: List[Dict[str, Any]] = []

    held = set(positions)
    picked = set()
    for s in signals[:10]:
        sym = s.get("symbol")
        picked.add(sym)
        w = float(s.get("weight", 1.0 / max(len(signals), 1)))
        act = "hold" if sym in held else "buy"
        actions.append({"symbol": sym, "action": act, "target_weight": round(w, 4),
                        "reason": "量化多因子综合打分靠前", "confidence": 0.55})
    for sym in held - picked:
        actions.append({"symbol": sym, "action": "reduce", "target_weight": 0.0,
                        "reason": "已跌出量化优选名单，降低暴露", "confidence": 0.5})

    mkt = context.get("market", {})
    pct = 0.0
    if isinstance(mkt, dict) and "上证指数" in mkt:
        pct = float(mkt["上证指数"].get("pct", 0.0))
    stance = "risk_on" if pct > 0.5 else ("risk_off" if pct < -1.0 else "neutral")

    return {
        "market_view": "（演示）无大盘数据时按中性处理；以量化信号为主、控制单票与换手。",
        "stance": stance,
        "actions": actions,
        "swing_ideas": [],
        "risk_notes": "这是无 API key 时的确定性兜底建议，仅演示链路，不构成投资建议。"
                      "实盘前请配置 ANTHROPIC_API_KEY 接入 Claude，并先用模拟盘验证。",
        "confidence": 0.4,
    }
