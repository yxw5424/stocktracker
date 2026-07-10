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
    """纯逻辑,可单测:back_ids/labels 等长列表 → (markdown, json_str, rows)。"""
    rows = []
    for i, bid in enumerate(back_ids):
        if not (bid or "").strip():
            continue
        label = labels[i] if i < len(labels) and labels[i] else f"策略{i + 1}"
        m = reader(bid.strip(), log=log) or {}
        m["_label"], m["_back_id"] = label, bid.strip()
        rows.append(m)

    if not rows:
        return "(没有可汇总的回测ID —— 请把各回测节点的「回测id」连到本节点)", "[]", []

    head = "| 策略 | " + " | ".join(n for _, n in _COLS) + " |"
    sep = "|" + "---|" * (len(_COLS) + 1)
    lines = [head, sep]
    for m in rows:
        lines.append("| " + m["_label"] + " | " +
                     " | ".join(_fmt(m.get(k)) for k, _ in _COLS) + " |")
    md = "\n".join(lines)
    js = json.dumps([{k: m.get(k) for k, _ in _COLS} | {"label": m["_label"], "back_id": m["_back_id"]}
                     for m in rows], ensure_ascii=False, default=str)
    return md, js, rows


# 平台把收益/回撤/波动率存成小数(0.594=59.4%);万一某些部署已是百分数,>5 就视为百分数。
_PCT_KEYS = {"back_profit", "back_profit_year", "max_drawdown", "volatility"}


def _fmt_html(key, v):
    if v is None or v == "":
        return "-"
    if key in _PCT_KEYS and isinstance(v, (int, float)):
        pct = v * 100 if abs(v) <= 5 else v
        return f"{pct:.2f}%"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def read_profit_series(back_id, log=None):
    """读每日累计收益序列 [(date, strategy%, benchmark%)];失败返回 []。"""
    try:
        import os
        from pymongo import MongoClient
        uri = os.getenv("MONGO_URI", "localhost:27017")
        kwargs = {"serverSelectionTimeoutMS": 3000}
        user, pwd = os.getenv("MONGO_USER", ""), os.getenv("MONGO_PASSWORD", "")
        if user and pwd:
            kwargs.update(username=user, password=pwd,
                          authSource=os.getenv("MONGO_AUTH_DB", "admin"))
        if os.getenv("MONGO_TYPE", "replica_set") == "replica_set":
            kwargs["replicaSet"] = os.getenv("MONGO_REPLICA_SET", "rs0")
        cli = MongoClient(uri if uri.startswith("mongodb") else f"mongodb://{uri}", **kwargs)
        db = cli[os.getenv("DATABASE_NAME", "panda")]
        docs = list(db["panda_backtest_profit"].find(
            {"back_id": back_id}, {"_id": 0, "gmt_create": 1, "strategy_profit": 1, "csi_stock": 1}
        ).sort("gmt_create", 1))
        cli.close()
        out = []
        for d in docs:
            sp, bm = d.get("strategy_profit"), d.get("csi_stock")
            if sp is None:
                continue
            out.append((str(d.get("gmt_create", "")), float(sp), float(bm) if bm is not None else None))
        return out
    except Exception as exc:
        if log:
            log(f"读取收益曲线失败(忽略):{exc}")
        return []


_PALETTE = ["#58a6ff", "#f6a33c", "#7ee2a8", "#e07be0", "#ff7b72", "#9ecbff"]


def _downsample(seq, n=420):
    if len(seq) <= n:
        return seq
    step = len(seq) / n
    return [seq[int(i * step)] for i in range(n)] + [seq[-1]]


def _svg_chart(series_map, width=980, height=380):
    """series_map: {label: [(date, val%), ...]}(已统一为百分数)。纯SVG,无外部依赖。"""
    series_map = {k: v for k, v in series_map.items() if v}
    if not series_map:
        return ""
    allv = [v for pts in series_map.values() for _, v in pts]
    vmin, vmax = min(allv + [0]), max(allv)
    pad = (vmax - vmin) * 0.06 or 1
    vmin, vmax = vmin - pad, vmax + pad
    L, R, T, B = 56, 16, 14, 30
    iw, ih = width - L - R, height - T - B

    def ypix(v):
        return T + ih * (1 - (v - vmin) / (vmax - vmin))

    parts = []
    # 网格 + Y轴标签
    for i in range(5):
        gv = vmin + (vmax - vmin) * i / 4
        y = ypix(gv)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{width-R}" y2="{y:.1f}" stroke="#232b3d"/>')
        parts.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" fill="#8892a6" font-size="11">{gv:.0f}%</text>')
    zero_y = ypix(0)
    parts.append(f'<line x1="{L}" y1="{zero_y:.1f}" x2="{width-R}" y2="{zero_y:.1f}" stroke="#3a4560" stroke-dasharray="3 3"/>')
    legend, li = [], 0
    for label, pts in series_map.items():
        pts = _downsample(pts)
        n = len(pts)
        color = "#8892a6" if label.startswith("基准") else _PALETTE[li % len(_PALETTE)]
        dash = ' stroke-dasharray="5 4"' if label.startswith("基准") else ""
        if not label.startswith("基准"):
            li += 1
        coords = " ".join(f"{L + iw * i / max(n-1,1):.1f},{ypix(v):.1f}" for i, (_, v) in enumerate(pts))
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.8"{dash}/>')
        legend.append((label, color, pts[-1][1]))
    # X轴首尾日期
    first_label = next(iter(series_map.values()))
    if first_label:
        parts.append(f'<text x="{L}" y="{height-8}" fill="#8892a6" font-size="11">{first_label[0][0]}</text>')
        parts.append(f'<text x="{width-R}" y="{height-8}" text-anchor="end" fill="#8892a6" font-size="11">{first_label[-1][0]}</text>')
    leg = "".join(
        f'<span style="margin-right:18px"><span style="display:inline-block;width:18px;height:3px;'
        f'background:{c};vertical-align:middle;margin-right:6px"></span>{lb}(终值 {fv:.1f}%)</span>'
        for lb, c, fv in legend)
    return (f'<div class=leg>{leg}</div>'
            f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px">{"".join(parts)}</svg>')


def _bars(rows):
    """夏普/回撤对比条形图(纯CSS)。"""
    def pct(v):
        return v * 100 if isinstance(v, (int, float)) and abs(v) <= 5 else (v or 0)
    sh = [(r["_label"], r.get("sharpe") or 0) for r in rows]
    dd = [(r["_label"], abs(pct(r.get("max_drawdown") or 0))) for r in rows]
    out = []
    for title, data, color, fmt in (("夏普比率(越高越好)", sh, "#58a6ff", "{:.2f}"),
                                    ("最大回撤%(越短越好)", dd, "#ff7b72", "{:.1f}%")):
        mx = max((abs(v) for _, v in data), default=1) or 1
        rows_html = "".join(
            f'<div class=brow><span class=blbl>{lb}</span>'
            f'<span class=bar><span style="width:{max(abs(v)/mx*100,2):.1f}%;background:{color}"></span></span>'
            f'<span class=bval>{fmt.format(v)}</span></div>' for lb, v in data)
        out.append(f'<div class=panel><h3>{title}</h3>{rows_html}</div>')
    return "".join(out)


def build_html(rows, series_map=None, title="回测汇总报告"):
    """自包含 HTML:指标表 + 条形图 + 收益曲线叠加。series_map 可为 None(只出表)。"""
    best_sharpe = None
    vals = [r.get("sharpe") for r in rows if isinstance(r.get("sharpe"), (int, float))]
    if vals:
        best_sharpe = max(vals)
    head = "".join(f"<th>{n}</th>" for _, n in _COLS)
    body = []
    for r in rows:
        is_best = best_sharpe is not None and r.get("sharpe") == best_sharpe
        tds = []
        for k, _ in _COLS:
            cell = _fmt_html(k, r.get(k))
            cls = ' class="dd"' if k == "max_drawdown" else ""
            tds.append(f"<td{cls}>{cell}</td>")
        body.append(f"<tr{' class=best' if is_best else ''}><td class=lbl>{r['_label']}"
                    f"{' 🏆' if is_best else ''}</td>{''.join(tds)}</tr>")
    chart = _svg_chart(series_map) if series_map else ""
    chart_html = f'<div class=panel style="width:100%"><h3>累计收益曲线</h3>{chart}</div>' if chart else ""
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:system-ui,'Microsoft YaHei',sans-serif;background:#0f1420;color:#dde3ee;
     display:flex;flex-direction:column;align-items:center;padding:32px 16px}}
h1{{font-size:20px;font-weight:600}}h3{{font-size:14px;color:#9daecd;margin:0 0 12px}}
p.hint{{color:#8892a6;font-size:13px}}
table{{border-collapse:collapse;margin-top:8px;font-size:14px}}
th,td{{padding:9px 14px;border-bottom:1px solid #232b3d;text-align:right;white-space:nowrap}}
th{{color:#8fa0bd;font-weight:600;border-bottom:2px solid #33405c}}
td.lbl{{text-align:left;font-weight:600}}td.dd{{color:#ff7b72}}
tr.best{{background:#12281c}}tr.best td{{color:#7ee2a8}}tr:hover{{background:#161e30}}
.wrap{{display:flex;flex-wrap:wrap;gap:16px;justify-content:center;max-width:1040px;margin-top:16px}}
.panel{{background:#141b2c;border:1px solid #232b3d;border-radius:10px;padding:16px 18px;min-width:300px}}
.brow{{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}}
.blbl{{width:150px;text-align:right;color:#aab6cc}}
.bar{{flex:1;height:12px;background:#0c1120;border-radius:6px;overflow:hidden;display:flex}}
.bar span{{height:100%;border-radius:6px}}
.bval{{width:64px}}.leg{{font-size:12.5px;color:#c6d0e2;margin-bottom:8px}}
</style></head><body>
<h1>{title}</h1>
<p class=hint>🏆 = 夏普最高;判稳定看「夏普 + 最大回撤」,别只看收益。</p>
<table><tr><th style="text-align:left">策略</th>{head}</tr>
{''.join(body)}</table>
<div class=wrap>{_bars(rows)}{chart_html}</div>
</body></html>"""


def write_index(rep_dir):
    """生成 /reports/index.html:按时间倒序列出全部报告。"""
    import os
    files = sorted((f for f in os.listdir(rep_dir) if f.startswith("summary_") and f.endswith(".html")),
                   reverse=True)
    items = "".join(f'<li><a href="{f}">{f}</a></li>' for f in files)
    open(os.path.join(rep_dir, "index.html"), "w", encoding="utf-8").write(
        f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>回测报告列表</title>
<style>body{{font-family:system-ui;background:#0f1420;color:#dde3ee;padding:40px}}
a{{color:#58a6ff;text-decoration:none;line-height:2}}h1{{font-size:18px}}</style></head>
<body><h1>回测报告(新→旧) · <a href="latest.html">直接看最新</a></h1><ul>{items}</ul></body></html>""")


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
        md, js, rows = build_summary(back_ids, labels, log=self.log_error)
        self.log_info("===== 回测汇总表(可直接复制) =====")
        for line in md.splitlines():
            self.log_info(line)
        self.log_info("===== 表结束 =====")

        # 可视化:指标表 + 条形图 + 收益曲线,写 HTML(时间戳版 + latest.html + index.html)
        if rows:
            try:
                import os as _os
                import time as _time
                # 收益曲线:每条策略一根线;基准(沪深300)取自最长的那条序列
                series_map, bench = {}, None
                for r in rows:
                    pts = read_profit_series(r["_back_id"], log=self.log_error)
                    if not pts:
                        continue
                    # 统一为百分数(平台存小数时 ×100)
                    mx = max(abs(v) for _, v, _ in pts) or 1
                    k = 100.0 if mx <= 20 else 1.0
                    series_map[r["_label"]] = [(d, v * k) for d, v, _ in pts]
                    bm = [(d, b * k) for d, v, b in pts if b is not None]
                    if bm and (bench is None or len(bm) > len(bench)):
                        bench = bm
                if bench:
                    series_map["基准·沪深300"] = bench
                html = build_html(rows, series_map)
                rep_dir = _os.getenv("REPORT_DIR", "/reports")
                _os.makedirs(rep_dir, exist_ok=True)
                fname = f"summary_{_time.strftime('%Y%m%d_%H%M%S')}.html"
                for name in (fname, "latest.html"):
                    with open(_os.path.join(rep_dir, name), "w", encoding="utf-8") as f:
                        f.write(html)
                write_index(rep_dir)
                self.log_info("📊 可视化报告(表+条形图+收益曲线)已生成:")
                self.log_info("   浏览器打开 http://127.0.0.1:8000/reports/latest.html (固定网址,永远是最新一次)")
                self.log_info(f"   历史列表 http://127.0.0.1:8000/reports/  |  本地文件 docker\\reports\\{fname}")
            except Exception as exc:
                self.log_error(f"HTML 报表生成失败(不影响汇总结果):{exc}")
        return SummaryOutput(summary_md=md, summary_json=js)


BacktestSummaryNode.set_short_description(
    "<p>把多条回测链的结果并成一张<strong>对比表</strong>(夏普/回撤/年化/成交数),整表打进运行日志。</p>")
BacktestSummaryNode.set_long_description(
    "把各「股票回测」的输出「回测id」连到 回测ID1..6,标签按顺序逗号分隔;"
    "所有回测跑完后自动汇总,不用手抄指标。")
