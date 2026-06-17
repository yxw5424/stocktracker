"""诚实的轻量回测:某"信号"在该股历史上出现后,未来 N 天的胜率/收益分布。

诚实化(对照旧版"裸算 closes[i+h]/closes[i]"的几处撒谎,逐条修正):
- **后复权(hfq)**:前复权会随新除权改写历史价 = look-ahead 泄漏;回测用 hfq。
- **T+1 成交**:信号当日定,**次日收盘才成交**(更接近真实;A股当天买不进当天的信号)。
- **真实成本**:每笔往返扣 cost_pct(印花税0.05%+佣金+滑点,默认 0.2%)。
- **置信区间**:胜率给 Wilson 区间、均值给 95% 区间;**样本 N<100 一律标注"不可信"**。
- **样本外**:同时给"全样本"和"最近30%(样本外)"两套——只信样本外那套。
- **幸存者偏差**:被回测的票本身是"还没退市/暴雷"的幸存者,历史天然偏乐观(免费源拿不到退市股)。

仍然:**历史 ≠ 未来;胜率高 ≠ 该买;这是统计画像不是买卖建议。**
"""
from __future__ import annotations

import math

import pandas as pd

from . import fetch as fetchmod


def _sig_big_up_volume(df: pd.DataFrame, up: float = 5.0, vol_mult: float = 2.0) -> pd.Series:
    ret = df["close"].pct_change() * 100
    vol_ma = df["volume"].rolling(20).mean()
    return (ret >= up) & (df["volume"] >= vol_mult * vol_ma)


def _sig_breakout(df: pd.DataFrame, n: int = 20) -> pd.Series:
    prev_high = df["close"].shift(1).rolling(n).max()
    return df["close"] > prev_high


def _sig_above_ma(df: pd.DataFrame, n: int = 20) -> pd.Series:
    ma = df["close"].rolling(n).mean()
    return (df["close"] > ma) & (df["close"].shift(1) <= ma.shift(1))  # 上穿


def _sig_reversal(df: pd.DataFrame, n: int = 5, drop: float = 8.0) -> pd.Series:
    """短期反转候选:近 n 日累计跌幅 ≥ drop%(buy-the-dip)。验证 A股'反转>动量'。"""
    cum = df["close"].pct_change(n) * 100
    return cum <= -drop


def _sig_reversal_sharp(df: pd.DataFrame, n: int = 3, drop: float = 6.0) -> pd.Series:
    return df["close"].pct_change(n) * 100 <= -drop


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean().replace(0, 1e-9)
    return 100 - 100 / (1 + up / dn)


def _sig_rsi_oversold(df: pd.DataFrame, n: int = 14, th: float = 30) -> pd.Series:
    r = _rsi(df["close"], n)
    return (r < th) & (r.shift(1) >= th)   # 刚跌入超卖


def _sig_momentum(df: pd.DataFrame, n: int = 20, up: float = 15.0) -> pd.Series:
    return df["close"].pct_change(n) * 100 >= up   # 强动量(追强)


def _sig_new_low(df: pd.DataFrame, n: int = 20) -> pd.Series:
    prev_low = df["close"].shift(1).rolling(n).min()
    return df["close"] < prev_low   # 创20日新低(抄新低)


def _sig_limit_up(df: pd.DataFrame) -> pd.Series:
    return df["close"].pct_change() * 100 >= 9.7   # 涨停(主板近似)


SIGNALS = {
    "big_up_volume": (_sig_big_up_volume, "放量大涨(涨幅≥5%且量≥2倍均量)= 追涨"),
    "momentum_20d": (_sig_momentum, "20日强动量(涨≥15%)= 追强"),
    "limit_up": (_sig_limit_up, "涨停(次日买入)= 打板接力"),
    "breakout_20d": (_sig_breakout, "突破20日新高"),
    "cross_ma20": (_sig_above_ma, "上穿20日均线 = 金叉"),
    "reversal_5d": (_sig_reversal, "短期反转(近5日跌≥8%)= 抄反转"),
    "reversal_sharp": (_sig_reversal_sharp, "急跌反转(近3日跌≥6%)"),
    "rsi_oversold": (_sig_rsi_oversold, "RSI跌破30(超卖)"),
    "new_low_20d": (_sig_new_low, "创20日新低(抄新低)"),
}


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """胜率的 Wilson 置信区间(比 p±1.96·se 在小样本下更可靠)。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((center - half) / denom * 100, (center + half) / denom * 100)


def _stats(rets: list[float]) -> dict | None:
    """rets 单位为 %。返回胜率(+Wilson区间)、均值(+区间)、盈亏比、期望等。"""
    n = len(rets)
    if n == 0:
        return None
    wins = sum(1 for r in rets if r > 0)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n if n > 1 else 0.0
    sd = math.sqrt(var)
    ci = 1.96 * sd / math.sqrt(n) if n > 0 else 0.0
    gains = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    pf = (sum(gains) / sum(losses)) if losses else float("inf")
    wlo, whi = _wilson(wins, n)
    s = sorted(rets)
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "win_ci": [round(wlo, 1), round(whi, 1)],
        "avg": round(mean, 2),
        "avg_ci": [round(mean - ci, 2), round(mean + ci, 2)],
        "median": round(s[n // 2], 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "best": round(max(rets), 2),
        "worst": round(min(rets), 2),
        "reliable": n >= 100,   # 低于100样本:区间太宽,不可信
    }


def backtest(code: str, signal: str = "big_up_volume",
             horizons=(1, 3, 5, 10), days: int = 800, cost_pct: float = 0.2) -> dict:
    if signal not in SIGNALS:
        raise ValueError(f"未知信号 {signal},可选:{list(SIGNALS)}")
    fn, desc = SIGNALS[signal]
    df = fetchmod.fetch_daily(code, days, adjust="hfq").reset_index(drop=True)   # 后复权,防泄漏
    if df.empty or len(df) < 60:
        return {"code": code, "signal": signal, "error": "历史数据不足(后复权<60根)"}

    mask = fn(df).fillna(False).to_numpy()
    close = df["close"].to_numpy(dtype=float)
    n_bars = len(df)
    cost = cost_pct / 100.0
    split = int(n_bars * 0.7)   # 后30% 作样本外

    # 信号在 i → T+1 次日收盘 entry=close[i+1],持有 h 天 exit=close[i+1+h],往返扣成本
    sig_idx = [i for i in range(n_bars - 1) if mask[i]]

    horizon_stats = {}
    for h in horizons:
        full, oos = [], []
        for i in sig_idx:
            entry, exit_ = i + 1, i + 1 + h
            if exit_ >= n_bars:
                continue
            net = (close[exit_] / close[entry] - 1 - cost) * 100   # 净收益%
            full.append(net)
            if entry >= split:        # entry 落在最近30% = 样本外
                oos.append(net)
        horizon_stats[f"{h}d"] = {"full": _stats(full), "oos": _stats(oos)}

    return {
        "code": code, "signal": signal, "signal_desc": desc,
        "sample_days": n_bars, "occurrences": len(sig_idx),
        "cost_pct": cost_pct, "t_plus_1": True, "adjust": "hfq",
        "horizons": horizon_stats,
        "caveats": [
            "T+1成交(信号次日收盘)、已扣往返成本%、后复权",
            "胜率带Wilson区间;N<100标为不可信(区间太宽)",
            "只信 oos(样本外/最近30%);full 为样本内,会偏乐观",
            "幸存者偏差:此票是未退市的幸存者,历史天然偏好",
            "历史≠未来;胜率高≠该买;非投资建议",
        ],
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

    ap = argparse.ArgumentParser(description="诚实轻量回测(后复权+T+1+成本+置信区间+样本外)")
    ap.add_argument("code")
    ap.add_argument("--signal", default="big_up_volume", choices=list(SIGNALS))
    ap.add_argument("--days", type=int, default=800)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = backtest(args.code, args.signal, days=args.days)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if "error" in res:
        print(f"{args.code}: {res['error']}")
        return

    print(f"{res['code']} 信号「{res['signal_desc']}」 — 近{res['sample_days']}根(后复权)出现 {res['occurrences']} 次"
          f"  [T+1成交·扣成本{res['cost_pct']}%]")
    print(f"{'持有':<5}{'样本':>5}{'样本外胜率(Wilson区间)':>26}{'样本外均值':>12}{'盈亏比':>8}{'最差':>8}")
    for h, hs in res["horizons"].items():
        o = hs["oos"]
        if not o:
            print(f"{h:<5}{'—':>5}  样本外无样本")
            continue
        flag = "" if o["reliable"] else "  ⚠样本少不可信"
        print(f"{h:<5}{o['n']:>5}{o['win_rate']:>9}% [{o['win_ci'][0]}~{o['win_ci'][1]}]"
              f"{o['avg']:>10}%{str(o['profit_factor']):>8}{o['worst']:>7}%{flag}")
    print("\n⚠️ " + ";".join(res["caveats"]))


if __name__ == "__main__":
    main()
