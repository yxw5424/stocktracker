"""advisor —— AI 投资顾问：日频上下文 → Claude 决策（含 mock 兜底）。"""
from .advisor import run_advisor, mock_advisor, DECISION_SCHEMA, MODEL
from .context import build_context, fetch_market_snapshot, fetch_news

__all__ = ["run_advisor", "mock_advisor", "DECISION_SCHEMA", "MODEL",
           "build_context", "fetch_market_snapshot", "fetch_news"]
