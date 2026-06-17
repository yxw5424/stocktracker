"""B:特征 + GBDT 模型层。把信号变成连续特征,walk-forward 样本外训练,诚实报 IC。

防泄漏纪律(命根子):
- 特征只用【≤t】的数据(rolling/shift);标签=从【t+1 买入、持有 h 天】的净收益(扣成本),
  与特征时间窗不重叠。
- 按【日期】时间切分(非随机):前 70% 训练、后 30% 测试;边界加 embargo(h+2 日)去掉
  标签跨越分界的样本。
- GBDT 不做标准化 → 避开"用未来数据归一化"这一泄漏源。
- 报【横截面 IC】(每个交易日内 pred 与未来收益的秩相关,再按日平均)。
  **月度个位数% 的 IC 是常态,别指望印钞机。** 历史≠未来;非投资建议。
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor

from .backtest import _rsi
from .xbacktest import build_panel

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

FEATURES = ["rev_5", "rev_20", "mom_20", "mom_60", "vol_20", "turn_ratio",
            "amplitude", "rsi_14", "dist_ma20", "dist_high20", "vol_chg5"]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """单只票的特征(每行=某交易日收盘后的决策点,只用过去数据)。"""
    c, v, h, lo = df["close"], df["volume"], df["high"], df["low"]
    ret1 = c.pct_change()
    return pd.DataFrame({
        "date": df["date"],
        "rev_5": -c.pct_change(5) * 100,          # 反转(越大=跌越多)
        "rev_20": -c.pct_change(20) * 100,
        "mom_20": c.pct_change(20) * 100,         # 动量
        "mom_60": c.pct_change(60) * 100,
        "vol_20": ret1.rolling(20).std() * 100,   # 波动
        "turn_ratio": v / v.rolling(20).mean(),   # 换手代理(量比)
        "amplitude": (h - lo) / c.shift(1) * 100,
        "rsi_14": _rsi(c, 14),
        "dist_ma20": (c / c.rolling(20).mean() - 1) * 100,
        "dist_high20": (c / c.shift(1).rolling(20).max() - 1) * 100,
        "vol_chg5": v.pct_change(5),
    })


def build_dataset(panel: dict, horizon: int = 10, cost_pct: float = 0.2) -> pd.DataFrame:
    cost = cost_pct / 100.0
    frames = []
    for code, df in panel.items():
        feat = build_features(df)
        c = df["close"]
        entry = c.shift(-1)                        # T+1 买入
        exit_ = c.shift(-(1 + horizon))            # 持有 h 天
        feat["label"] = (exit_ / entry - 1 - cost) * 100
        feat["code"] = code
        frames.append(feat)
    ds = pd.concat(frames, ignore_index=True)
    ds = ds.dropna(subset=FEATURES + ["label"])
    ds = ds[np.isfinite(ds[FEATURES + ["label"]]).all(axis=1)]
    return ds


def _rank_ic(df: pd.DataFrame) -> tuple[float, int]:
    """横截面 IC:每个交易日内 pred 与 label 的秩相关,再按日平均。"""
    ics = []
    for _, g in df.groupby("date"):
        if len(g) >= 5:
            ic = spearmanr(g["pred"], g["label"]).correlation
            if ic == ic:
                ics.append(ic)
    return (float(np.mean(ics)) if ics else float("nan")), len(ics)


def train_eval(ds: pd.DataFrame, horizon: int = 10, frac: float = 0.7) -> dict:
    dates = sorted(ds["date"].unique())
    split = dates[int(len(dates) * frac)]
    embargo = dates[max(0, dates.index(split) - (horizon + 2))]   # 边界去重叠
    train = ds[ds["date"] < embargo]
    test = ds[ds["date"] >= split]
    if len(train) < 500 or len(test) < 200:
        return {"error": f"样本不足 train={len(train)} test={len(test)}"}

    model = HistGradientBoostingRegressor(max_iter=300, max_depth=4,
                                          learning_rate=0.05, l2_regularization=1.0)
    model.fit(train[FEATURES], train["label"])

    test = test.copy()
    test["pred"] = model.predict(test[FEATURES])
    ic, n_days = _rank_ic(test)

    q = test["pred"].quantile([0.1, 0.9])
    top = test[test["pred"] >= q[0.9]]["label"]
    bot = test[test["pred"] <= q[0.1]]["label"]
    return {
        "model": model, "split_date": str(split),
        "n_train": len(train), "n_test": len(test),
        "rank_ic": round(ic, 4), "ic_days": n_days,
        "top_decile_avg": round(float(top.mean()), 2),
        "bot_decile_avg": round(float(bot.mean()), 2),
        "long_short_spread": round(float(top.mean() - bot.mean()), 2),
        "top_decile_win": round(float((top > 0).mean()) * 100, 1),
        "horizon": horizon,
    }


def run(horizon: int = 10, days: int = 800) -> dict:
    from . import universe as uni
    panel = build_panel(uni.hs300(), days)
    ds = build_dataset(panel, horizon)
    res = train_eval(ds, horizon)
    res["universe"] = len(panel)
    res["dataset_rows"] = len(ds)
    res["features"] = FEATURES
    return res


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="GBDT 样本外建模(沪深300)")
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--days", type=int, default=800)
    args = ap.parse_args()

    r = run(args.horizon, args.days)
    if "error" in r:
        print(r["error"])
        return
    print(f"\nGBDT 模型 @ 沪深{r['universe']}只,{r['dataset_rows']} 个样本,{args.horizon}日标签")
    print(f"  训练/测试切分日: {r['split_date']}  (train {r['n_train']} / test {r['n_test']})")
    print(f"  样本外 横截面IC: {r['rank_ic']}  (覆盖 {r['ic_days']} 个交易日)")
    print(f"  Top10%预测 未来{args.horizon}日均值: {r['top_decile_avg']}%  胜率 {r['top_decile_win']}%")
    print(f"  Bottom10%预测 均值: {r['bot_decile_avg']}%")
    print(f"  多空价差(Top−Bottom): {r['long_short_spread']}%")
    print("\n⚠️ IC 个位数百分比是常态,非印钞机;幸存者偏差使数字偏乐观;历史≠未来;非投资建议。")


if __name__ == "__main__":
    main()
