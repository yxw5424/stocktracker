"""轻量回测:某"信号"在该股历史上出现后,未来 N 天的胜率 / 平均收益 / 分布。

诚实声明(务必随结果一起展示):
- 这是【历史统计】,**历史 ≠ 未来**;样本可能很小;**未计交易成本、滑点、涨跌停无法成交**。
- 胜率高 ≠ 该买;它只回答"过去这类形态之后,涨的次数占比多少",是参考不是建议。
- 用于"异动分初筛 → 候选轻量回测"的第二步:先选出异动候选,再看它们的历史成色。
"""
from __future__ import annotations

import pandas as pd

from . import fetch as fetchmod

# 可扩展的信号库:名字 -> (生成布尔信号列的函数, 中文说明)
def _sig_big_up_volume(df: pd.DataFrame, up: float = 5.0, vol_mult: float = 2.0) -> pd.Series:
    ret = df["close"].pct_change() * 100
    vol_ma = df["volume"].rolling(20).mean()
    return (ret >= up) & (df["volume"] >= vol_mult * vol_ma)


def _sig_breakout(df: pd.DataFrame, n: int = 20) -> pd.Series:
    prev_high = df["close"].shift(1).rolling(n).max()
    return df["close"] > prev_high


def _sig_limit_up(df: pd.DataFrame) -> pd.Series:
    ret = df["close"].pct_change() * 100
    return ret >= 9.7  # 主板涨停近似(双创/北交所阈值不同,这里粗略)


SIGNALS = {
    "big_up_volume": (_sig_big_up_volume, "单日放量大涨(涨幅≥5% 且量≥2倍20日均量)"),
    "breakout_20d": (_sig_breakout, "突破20日新高"),
    "limit_up": (_sig_limit_up, "涨停(主板近似)"),
}


def backtest(code: str, signal: str = "big_up_volume",
             horizons=(1, 3, 5, 10), days: int = 500) -> dict:
    """对单只票回测某信号。返回各持有期的胜率/平均收益等。"""
    if signal not in SIGNALS:
        raise ValueError(f"未知信号 {signal},可选:{list(SIGNALS)}")
    fn, desc = SIGNALS[signal]
    df = fetchmod.fetch_daily(code, days).reset_index(drop=True)
    if df.empty or len(df) < 30:
        return {"code": code, "signal": signal, "error": "历史数据不足"}

    mask = fn(df).fillna(False).to_numpy()
    closes = df["close"].to_numpy(dtype=float)
    idxs = [i for i in range(len(df)) if mask[i]]

    horizon_stats = {}
    for h in horizons:
        rets = [closes[i + h] / closes[i] - 1 for i in idxs if i + h < len(closes)]
        rets = [r * 100 for r in rets]
        if rets:
            rets_sorted = sorted(rets)
            horizon_stats[f"{h}d"] = {
                "n": len(rets),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "avg": round(sum(rets) / len(rets), 2),
                "median": round(rets_sorted[len(rets_sorted) // 2], 2),
                "best": round(max(rets), 2),
                "worst": round(min(rets), 2),
            }
    return {
        "code": code, "signal": signal, "signal_desc": desc,
        "sample_days": len(df), "occurrences": len(idxs),
        "horizons": horizon_stats,
        "disclaimer": "历史统计,非未来保证;未计成本/滑点/涨跌停;胜率高≠该买。",
    }


def main() -> None:
    import argparse
    import json
    import sys

    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="单只票信号轻量回测(历史≠未来)")
    ap.add_argument("code", help="6 位代码,如 600909")
    ap.add_argument("--signal", default="big_up_volume", choices=list(SIGNALS))
    ap.add_argument("--days", type=int, default=500)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = backtest(args.code, args.signal, days=args.days)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if "error" in res:
        print(f"{args.code}: {res['error']}")
        return
    print(f"{res['code']} 信号「{res['signal_desc']}」 — 近{res['sample_days']}日出现 {res['occurrences']} 次")
    print(f"{'持有期':<6}{'样本':>5}{'胜率':>8}{'平均':>9}{'中位':>9}{'最好':>9}{'最差':>9}")
    for h, s in res["horizons"].items():
        print(f"{h:<6}{s['n']:>5}{s['win_rate']:>7}%{s['avg']:>8}%{s['median']:>8}%{s['best']:>8}%{s['worst']:>8}%")
    print(f"\n⚠️ {res['disclaimer']}")


if __name__ == "__main__":
    main()
