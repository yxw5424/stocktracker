"""横截面回测:一个信号在一个【股票池 × 全历史】上的真实统计(样本量够,胜率才有意义)。

为什么要横截面:单票信号样本太少(几年才出十几次),胜率置信区间宽到无意义。把信号
放到沪深300 × 几年上,样本上千,胜率才有置信度。

最关键的一条:**对比基线(同期任意持有 h 天的平均结果)**,
  edge = 信号均值 − 基线均值。
**没有 edge 的信号,胜率再高也只是跟着大盘涨,不是 alpha。**

仍然:后复权 + T+1 成交 + 扣成本;历史 ≠ 未来;非投资建议。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pandas as pd

from . import fetch as fetchmod
from . import universe as uni
from .backtest import SIGNALS, _stats

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL_DIR = os.path.join(ROOT, ".cache", "panel")


def _panel_one(code: str, days: int, today: str):
    """单只票的后复权日线:当天缓存命中直接读,否则抓一次并缓存。"""
    path = os.path.join(PANEL_DIR, f"{code}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            if obj.get("date") == today:
                return pd.DataFrame(obj["rows"])
        except Exception:
            pass
    df = fetchmod.fetch_daily(code, days, adjust="hfq")
    os.makedirs(PANEL_DIR, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": today, "rows": df.to_dict("records")}, f, default=str)
    except Exception:
        pass
    return df


def build_panel(codes: list[str], days: int = 800, today: str | None = None) -> dict:
    """构建面板 {code: df}。首次抓取较慢(逐只直连+缓存),之后走当天缓存秒回。"""
    today = today or str(dt.date.today())
    panel = {}
    for i, code in enumerate(codes):
        try:
            df = _panel_one(code, days, today)
            if df is not None and len(df) >= 80:
                panel[code] = df.reset_index(drop=True)
        except Exception as e:
            print(f"[panel] {code} fail: {e}", flush=True)
        if (i + 1) % 25 == 0:
            print(f"[panel] {i + 1}/{len(codes)} 已取 {len(panel)}", flush=True)
    return panel


def cross_backtest(signal: str = "big_up_volume", codes: list[str] | None = None,
                   horizons=(1, 3, 5, 10), days: int = 800, cost_pct: float = 0.2) -> dict:
    fn, desc = SIGNALS[signal]
    codes = codes or uni.hs300()
    panel = build_panel(codes, days)
    cost = cost_pct / 100.0

    res = {"signal": signal, "signal_desc": desc, "universe": len(panel),
           "days": days, "horizons": {}}
    for h in horizons:
        sig_rets, base_rets = [], []
        for df in panel.values():
            close = df["close"].to_numpy(dtype=float)
            mask = fn(df).fillna(False).to_numpy()
            n = len(close)
            for i in range(n - 1):
                entry, exit_ = i + 1, i + 1 + h
                if exit_ >= n:
                    continue
                r = (close[exit_] / close[entry] - 1 - cost) * 100   # T+1 + 扣成本
                base_rets.append(r)
                if mask[i]:
                    sig_rets.append(r)
        s, b = _stats(sig_rets), _stats(base_rets)
        edge = round(s["avg"] - b["avg"], 2) if (s and b) else None
        res["horizons"][f"{h}d"] = {"signal": s, "baseline": b, "edge": edge}
    return res


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="横截面回测(默认沪深300)")
    ap.add_argument("--signal", default="big_up_volume", choices=list(SIGNALS))
    ap.add_argument("--days", type=int, default=800)
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 只(测试用)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    codes = uni.hs300()
    if args.limit:
        codes = codes[:args.limit]
    res = cross_backtest(args.signal, codes, days=args.days)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print(f"\n信号「{res['signal_desc']}」 横截面 @ {res['universe']}只 × 近{args.days}根"
          f"(后复权·T+1·扣成本0.2%)")
    print(f"{'持有':<5}{'信号N':>8}{'信号胜率(Wilson区间)':>24}{'信号均值':>10}{'基线均值':>10}{'EDGE':>9}")
    for h, hs in res["horizons"].items():
        s, b = hs["signal"], hs["baseline"]
        if not s or not b:
            continue
        print(f"{h:<5}{s['n']:>8}{s['win_rate']:>9}% [{s['win_ci'][0]}~{s['win_ci'][1]}]"
              f"{s['avg']:>9}%{b['avg']:>9}%{str(hs['edge']):>8}%")
    print("\n⚠️ EDGE=信号均值−基线均值;**EDGE≈0或负 = 信号没有超额,只是跟大盘**。"
          "已扣成本、T+1、后复权;历史≠未来;非投资建议。")


if __name__ == "__main__":
    main()
