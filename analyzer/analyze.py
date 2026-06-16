"""从分钟K线计算指标(涨跌幅 / 斜率 / 斜率加速度 / 量比 / 突破)并判断触发。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _slope_pct_per_hour(times: pd.Series, closes: pd.Series) -> float:
    """对窗口做线性回归,返回 %/小时 的斜率(相对均价归一化)。"""
    if len(closes) < 2:
        return 0.0
    t0 = times.iloc[0]
    x_hours = (times - t0).dt.total_seconds().to_numpy() / 3600.0
    y = closes.to_numpy(dtype=float)
    if np.ptp(x_hours) == 0:
        return 0.0
    a, _ = np.polyfit(x_hours, y, 1)  # 价格变化 / 小时
    ref = y.mean()
    return float(a / ref * 100.0) if ref else 0.0


def analyze(df: pd.DataFrame, cfg: dict, prev_close: float | None = None) -> dict:
    win = int(cfg["analysis"].get("slope_window", 8))
    closes, times = df["close"], df["time"]
    latest = float(closes.iloc[-1])

    # 斜率 + 斜率加速度(当前窗口 vs 右移一格的窗口)
    seg = df.tail(win)
    slope = _slope_pct_per_hour(seg["time"], seg["close"])
    prev_seg = df.iloc[-(win + 1):-1] if len(df) > win else seg
    slope_prev = _slope_pct_per_hour(prev_seg["time"], prev_seg["close"])
    accel = slope - slope_prev

    # 区间涨跌幅(window_minutes 内)+ 日内涨跌幅(昨收)
    wmin = int(cfg["triggers"]["pct_change"].get("window_minutes", 60))
    cutoff = times.iloc[-1] - pd.Timedelta(minutes=wmin)
    base_window = df.loc[times <= cutoff]
    base_price = float(base_window["close"].iloc[-1]) if not base_window.empty else float(closes.iloc[0])
    pct_window = (latest / base_price - 1) * 100 if base_price else 0.0
    pct_day = (latest / prev_close - 1) * 100 if prev_close else None

    # 量比
    vol = df["volume"]
    cur_vol = float(vol.iloc[-1])
    ref_vol = float(vol.iloc[-(win + 1):-1].mean()) if len(vol) > 1 else cur_vol
    vol_ratio = cur_vol / ref_vol if ref_vol else 1.0

    return {
        "price": round(latest, 3),
        "pct_window": round(pct_window, 2),
        "pct_window_minutes": wmin,
        "pct_day": round(pct_day, 2) if pct_day is not None else None,
        "slope": round(slope, 2),
        "slope_prev": round(slope_prev, 2),
        "accel": round(accel, 2),
        "vol_ratio": round(vol_ratio, 2),
        "bar_time": str(times.iloc[-1]),
    }


def evaluate_triggers(metrics: dict, target: dict, cfg: dict) -> list[dict]:
    """返回触发的告警 [{type, level, message}]。"""
    tr = cfg["triggers"]
    alerts: list[dict] = []

    if tr["pct_change"]["enabled"]:
        th = float(tr["pct_change"]["threshold_pct"])
        if abs(metrics["pct_window"]) >= th:
            direction = "上涨" if metrics["pct_window"] > 0 else "下跌"
            alerts.append({
                "type": "pct_change",
                "level": "high" if abs(metrics["pct_window"]) >= 1.7 * th else "normal",
                "message": f"{metrics['pct_window_minutes']}分钟{direction} {metrics['pct_window']:+.2f}%(阈值±{th}%)",
            })

    if tr["slope_surge"]["enabled"]:
        sth = float(tr["slope_surge"]["slope_threshold"])
        ath = float(tr["slope_surge"]["accel_threshold"])
        if abs(metrics["slope"]) >= sth or abs(metrics["accel"]) >= ath:
            alerts.append({
                "type": "slope_surge",
                "level": "high",
                "message": f"斜率激增 {metrics['slope']:+.2f}%/h(加速度{metrics['accel']:+.2f})→ 切到高频节奏",
            })

    if tr["volume_spike"]["enabled"]:
        vth = float(tr["volume_spike"]["ratio_threshold"])
        if metrics["vol_ratio"] >= vth:
            alerts.append({
                "type": "volume_spike",
                "level": "normal",
                "message": f"放量异动:量比 {metrics['vol_ratio']:.2f}(阈值{vth})",
            })

    if tr["breakout"]["enabled"]:
        for lvl in target.get("levels") or []:
            lvl = float(lvl)
            if metrics["price"] >= lvl and metrics["slope"] > 0:
                alerts.append({"type": "breakout", "level": "high", "message": f"上穿关键价位 {lvl}"})
            elif metrics["price"] <= lvl and metrics["slope"] < 0:
                alerts.append({"type": "breakout", "level": "high", "message": f"下破关键价位 {lvl}"})

    return alerts
