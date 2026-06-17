"""多维信号引擎入口:1~2 个请求拿【全市场 + 指数】→ 多维信号 → 信息筛选 → market.json。

用法:
    python -m analyzer.digest          # 打印市场形势 + 筛选后的多维信号
    python -m analyzer.digest --json   # 写 docs/data/market.json 供看板用

加新维度:在本文件写一个 @sig.register def collect_xxx(ctx): -> [Signal] 即可。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

import yaml

from . import fetch as fetchmod
from . import market as mkt
from . import providers as prov
from . import rules as rulesmod
from . import screen as screenmod
from . import signals as sig
from .signals import Signal

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")


@dataclass
class Ctx:
    provider: object
    spot: object
    idx: object
    breadth: dict
    indices: list
    regime: dict
    watchlist: list
    rules: list


# ─────────── 维度采集器(加新维度 = 加一个 @sig.register 函数)───────────

@sig.register
def collect_regime(ctx) -> list:
    r, bd = ctx.regime, ctx.breadth
    lvl = "high" if r["tone"] == "risk_off" else "notice"
    return [Signal("market", "regime", "MARKET", lvl, 60 + abs(r["index_avg_pct"]) * 5,
                   f"市场形势:{r['label']}(赚钱效应 {r['money_effect']}/100;"
                   f"涨{bd['adv']}/跌{bd['dec']},涨停{bd['limit_up']}/跌停{bd['limit_down']})", r)]


@sig.register
def collect_indices(ctx) -> list:
    return [Signal("market", "index", i["code"], "notice", 40 + abs(i["pct"]) * 4,
                   f"{i['name']} {i['pct']:+.2f}%", i)
            for i in ctx.indices if abs(i["pct"]) >= 1.0]


@sig.register
def collect_anomaly(ctx) -> list:
    d = screenmod.anomaly_score(ctx.spot)
    d = d[~d["name"].astype(str).str.contains("ST", case=False, na=False)]
    d = d[d["amount"] >= 5e7].sort_values("score", ascending=False).head(15)
    return [Signal("stock", "anomaly", r["code"], "notice", float(r["score"]),
                   f"{r['name']} {r['code']} {r['pct']:+.2f}% 振幅{r['amplitude']:.1f}% 异动分{r['score']:.0f}",
                   {"pct": float(r["pct"]), "amplitude": float(r["amplitude"]), "amount": float(r["amount"])})
            for _, r in d.iterrows()]


@sig.register
def collect_watchlist(ctx) -> list:
    if not ctx.watchlist:
        return []
    d = ctx.spot[ctx.spot["code"].isin(ctx.watchlist)]
    out = []
    for _, r in d.iterrows():
        pct = float(r["pct"])
        out.append(Signal("watchlist", "quote", r["code"],
                          "high" if abs(pct) >= 3 else "info", 70 + abs(pct) * 3,
                          f"⭐{r['name']} {r['code']} {pct:+.2f}% 现价{r['price']}",
                          {"pct": pct, "price": float(r["price"])}))
    return out


@sig.register
def collect_rules(ctx) -> list:
    """提示词规则(scope=all)在全市场上的命中。"""
    out = []
    for r in [x for x in ctx.rules if x.get("scope", "all") == "all"]:
        hit = rulesmod.match_dataframe(r, ctx.spot)
        if hit is None or hit.empty:
            continue
        hit = hit[~hit["name"].astype(str).str.contains("ST", case=False, na=False)]
        for _, row in hit.sort_values("amount", ascending=False).head(6).iterrows():
            out.append(Signal("rule", r["id"], row["code"], "high", 95,
                              f"📐规则「{r['name']}」命中:{row['name']} {row['code']} {row['pct']:+.2f}%",
                              {"rule": r["id"], "pct": float(row["pct"])}))
    return out


# ─────────────────────────── 编排 ───────────────────────────

def build_ctx(provider_name: str = "sina", watchlist: list | None = None) -> Ctx:
    p = prov.get_provider(provider_name)
    spot = p.spot_all()          # 1 请求(全市场)
    idx = p.indices()            # 1 请求(指数)
    bd = mkt.breadth(spot)
    idx_list = mkt.index_summary(idx)
    rg = mkt.regime(bd, idx_list)
    return Ctx(p, spot, idx, bd, idx_list, rg, list(watchlist or []), rulesmod.load_rules())


def _watchlist_codes() -> list:
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return [fetchmod._sina_symbol(t["code"]) for t in cfg.get("targets", [])]


def main() -> None:
    ap = argparse.ArgumentParser(description="多维信号引擎(市场形势 + 异动 + 自选)")
    ap.add_argument("--json", action="store_true", help="写 docs/data/market.json")
    args = ap.parse_args()

    ctx = build_ctx("sina", _watchlist_codes())
    digest = sig.fuse(ctx)
    out = {"regime": ctx.regime, "breadth": ctx.breadth, "indices": ctx.indices, "signals": digest}

    if args.json:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "market.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        print(f"wrote market.json: regime={ctx.regime['label']} signals={len(digest)}")
    else:
        bd, rg = ctx.breadth, ctx.regime
        print(f"形势:{rg['label']}　赚钱效应 {rg['money_effect']}/100")
        print(f"宽度:涨{bd['adv']} 跌{bd['dec']} 涨停{bd['limit_up']} 跌停{bd['limit_down']} 总额{bd['total_amount_yi']:.0f}亿")
        print("指数:" + "  ".join(f"{i['name']}{i['pct']:+.2f}%" for i in ctx.indices))
        print(f"\n筛选后 Top {len(digest)} 信号:")
        for s in digest:
            print(f"  [{s['dim']}/{s['level']}] {s['message']} (score {s['score']:.0f})")


if __name__ == "__main__":
    main()
