# -*- coding: utf-8 -*-
"""
回测汇总表节点 —— 把同一工作流里多条回测链的结果并成一张对比表。

用法:把各「股票回测」节点的输出「回测id」分别连到本节点的 回测ID1..6,
在「标签」里按同样顺序写名字(逗号分隔,如 "A1基准,A2风控,A3快出,D样本外")。
本节点会在所有回测跑完后执行,读取每个回测的绩效指标,输出:
  - summary_md   Markdown 对比表(同时整表打进运行日志,直接复制)
  - summary_json 结构化 JSON(给下游节点/程序用)

放到 panda_quantflow/src/panda_plugins/custom/ 下,与 ai_advisor_node.py 同目录。
"""
from typing import Optional, Type

from panda_plugins.base import BaseWorkNode, work_node, ui
from pydantic import BaseModel, Field

import json

from ai_advisor_node import read_backtest


_COLS = [
    ("back_profit", "总收益"),
    ("back_profit_year", "年化"),
    ("sharpe", "夏普"),
    ("max_drawdown", "最大回撤"),
    ("volatility", "波动率"),
    ("sortino", "索提诺"),
    ("trade_count", "成交笔数"),
    ("start_date", "开始"),
    ("end_date", "结束"),
]


def _fmt(v):
    if v is None or v == "":
        return "-"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def build_summary(back_ids, labels, reader=read_backtest, log=None):
    """纯逻辑,可单测:back_ids/labels 等长列表 → (markdown, json_str)。"""
    rows = []
    for i, bid in enumerate(back_ids):
        if not (bid or "").strip():
            continue
        label = labels[i] if i < len(labels) and labels[i] else f"策略{i + 1}"
        m = reader(bid.strip(), log=log) or {}
        m["_label"], m["_back_id"] = label, bid.strip()
        rows.append(m)

    if not rows:
        return "(没有可汇总的回测ID —— 请把各回测节点的「回测id」连到本节点)", "[]"

    head = "| 策略 | " + " | ".join(n for _, n in _COLS) + " |"
    sep = "|" + "---|" * (len(_COLS) + 1)
    lines = [head, sep]
    for m in rows:
        lines.append("| " + m["_label"] + " | " +
                     " | ".join(_fmt(m.get(k)) for k, _ in _COLS) + " |")
    md = "\n".join(lines)
    js = json.dumps([{k: m.get(k) for k, _ in _COLS} | {"label": m["_label"], "back_id": m["_back_id"]}
                     for m in rows], ensure_ascii=False, default=str)
    return md, js


@ui(
    labels={"input_type": "text_field", "placeholder": "标签,逗号分隔,与回测ID顺序对应", "allow_link": False},
    back_id_1={"input_type": "None"},
    back_id_2={"input_type": "None"},
    back_id_3={"input_type": "None"},
    back_id_4={"input_type": "None"},
    back_id_5={"input_type": "None"},
    back_id_6={"input_type": "None"},
)
class SummaryInput(BaseModel):
    labels: str = Field(default="", title="标签(逗号分隔)")
    back_id_1: str = Field(default="", title="回测ID1")
    back_id_2: str = Field(default="", title="回测ID2")
    back_id_3: str = Field(default="", title="回测ID3")
    back_id_4: str = Field(default="", title="回测ID4")
    back_id_5: str = Field(default="", title="回测ID5")
    back_id_6: str = Field(default="", title="回测ID6")


class SummaryOutput(BaseModel):
    summary_md: str = Field(default="", title="对比表(Markdown)")
    summary_json: str = Field(default="[]", title="对比数据(JSON)")


@work_node(name="回测汇总表", group="05-回测相关", type="general", box_color="purple")
class BacktestSummaryNode(BaseWorkNode):

    @classmethod
    def input_model(cls) -> Optional[Type[BaseModel]]:
        return SummaryInput

    @classmethod
    def output_model(cls) -> Optional[Type[BaseModel]]:
        return SummaryOutput

    def run(self, input: BaseModel) -> BaseModel:
        back_ids = [input.back_id_1, input.back_id_2, input.back_id_3,
                    input.back_id_4, input.back_id_5, input.back_id_6]
        labels = [x.strip() for x in (input.labels or "").replace("，", ",").split(",")]
        md, js = build_summary(back_ids, labels, log=self.log_error)
        self.log_info("===== 回测汇总表(可直接复制) =====")
        for line in md.splitlines():
            self.log_info(line)
        self.log_info("===== 表结束 =====")
        return SummaryOutput(summary_md=md, summary_json=js)


BacktestSummaryNode.set_short_description(
    "<p>把多条回测链的结果并成一张<strong>对比表</strong>(夏普/回撤/年化/成交数),整表打进运行日志。</p>")
BacktestSummaryNode.set_long_description(
    "把各「股票回测」的输出「回测id」连到 回测ID1..6,标签按顺序逗号分隔;"
    "所有回测跑完后自动汇总,不用手抄指标。")
