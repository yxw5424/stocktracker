# -*- coding: utf-8 -*-
"""
行情数据引擎(quantflow 容器内使用)—— 给看板的「自选管理/一键更新」提供:
  · norm_symbol / 板块涨跌停带宽(与 loader 同规则,勿改一处漏一处)
  · fetch_daily:东财→新浪双源日线(股票/ETF)
  · incremental_update:增量更新 —— 只拉库里最后日期之后的数据;
    ⚠ 前复权陷阱:分红/拆分后历史复权价整体平移,增量直接接上会出现假跳空。
    因此每次带 10 天重叠区比对,漂移超 0.2% 自动触发该标的全量重拉。
  · add_symbol:新增自选(归一化→判类型→全量拉取→写行情/名称/自选表)
全量首灌仍走 loader(LOAD_ALL=1);本模块管日常与前端操作。
"""
import os
import time
import datetime

import pymongo

FULL_START = os.getenv("FULL_START", "20210101")
WATCH_COL = "user_watchlist"

_ETF20_BUILTIN = {"159915", "159952", "159948", "159957", "159966", "159967"}

_COLMAP = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
           "最低": "low", "成交量": "volume", "成交额": "turnover"}


def norm_symbol(code):
    code = str(code).strip().upper()
    if not code:
        return ""
    if "." in code:
        return code
    if code.isalpha():                # 字母代码=美股 ticker(AAPL→AAPL.US)
        return code + ".US"
    if code[0] == "8" or code[:3] in ("920", "430"):
        return code + ".BJ"
    if code[0] in ("5", "6", "9"):
        return code + ".SH"
    return code + ".SZ"


def is_us(sym):
    return str(sym).upper().endswith(".US")


def bare(sym):
    return sym.split(".")[0]


def is_etf(sym):
    b = bare(sym)
    return b.startswith("5") or b[:2] in ("15", "16")


def limit_band(sym, etf):
    b = bare(sym)
    if is_us(sym):
        return 0.90                   # 美股无涨跌停;写±90%宽带让引擎校验形同虚设
    if etf:
        etf20 = _ETF20_BUILTIN | {x.strip() for x in os.getenv("ETF20", "").split(",") if x.strip()}
        return 0.20 if (b.startswith("588") or b in etf20) else 0.10
    if sym.endswith(".BJ"):
        return 0.30
    if b.startswith(("688", "689", "300", "301", "302")):
        return 0.20
    return 0.10


def _retry(fn, tries=2, delay=1.5):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(delay)
                delay *= 2
    raise last


def _fetch_daily_us(sym, start, end):
    """美股日线:yfinance(雅虎,含复权);国内环境需容器配代理(DOCKER_PROXY)。"""
    import yfinance as yf
    t = yf.Ticker(bare(sym))
    df = t.history(start=f"{start[:4]}-{start[4:6]}-{start[6:8]}",
                   end=f"{end[:4]}-{end[4:6]}-{end[6:8]}", auto_adjust=True)
    if df is None or df.empty:
        return df
    df = df.reset_index()
    df["date"] = df["Date"].astype(str).str[:10]
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Volume": "volume"})
    df["turnover"] = df["close"] * df["volume"]
    return df[["date", "open", "high", "low", "close", "volume", "turnover"]]


def _fetch_daily_us_stooq(sym, start, end):
    """美股备胎源:Stooq 免费日线 CSV(亦为复权价)。"""
    import io
    import requests
    import pandas as pd
    url = (f"https://stooq.com/q/d/l/?s={bare(sym).lower()}.us&i=d"
           f"&d1={start}&d2={end}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    if not r.text or r.text.startswith("No data") or r.text.lstrip().startswith("<"):
        raise RuntimeError("Stooq 返回非CSV(可能触发反爬验证,换网络环境或稍后再试)")
    df = pd.read_csv(io.StringIO(r.text))
    df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                            "Low": "low", "Close": "close", "Volume": "volume"})
    df["date"] = df["date"].astype(str)
    df["volume"] = df.get("Volume", df.get("volume", 0)).fillna(0) if "volume" in df else 0
    df["turnover"] = df["close"] * df["volume"]
    return df[["date", "open", "high", "low", "close", "volume", "turnover"]]


def fetch_daily(sym, start, end, etf):
    """拉日线:A股走东财→新浪,美股(.US)走yfinance→Stooq。返回标准列 DataFrame。"""
    if is_us(sym):
        try:
            return _retry(lambda: _fetch_daily_us(sym, start, end), tries=2)
        except Exception:
            return _retry(lambda: _fetch_daily_us_stooq(sym, start, end), tries=3)
    import akshare as ak
    b = bare(sym)

    if etf:
        def em():
            df = ak.fund_etf_hist_em(symbol=b, period="daily",
                                     start_date=start, end_date=end, adjust="qfq")
            if df is None or df.empty:
                return df
            df = df.rename(columns=_COLMAP)
            df["volume"] = df["volume"].astype(float) * 100.0
            return df

        def sina():
            pre = {"SH": "sh", "SZ": "sz"}.get(sym.rsplit(".", 1)[1], "sh")
            df = ak.fund_etf_hist_sina(symbol=f"{pre}{b}")
            if df is None or df.empty:
                return df
            df["date"] = df["date"].astype(str)
            d0 = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
            d1 = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
            return df[(df["date"] >= d0) & (df["date"] <= d1)].reset_index(drop=True)
    else:
        def em():
            df = ak.stock_zh_a_hist(symbol=b, period="daily",
                                    start_date=start, end_date=end, adjust="qfq")
            if df is None or df.empty:
                return df
            df = df.rename(columns=_COLMAP)
            df["volume"] = df["volume"].astype(float) * 100.0
            return df

        def sina():
            pre = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[sym.rsplit(".", 1)[1]]
            df = ak.stock_zh_a_daily(symbol=f"{pre}{b}", start_date=start,
                                     end_date=end, adjust="qfq")
            if df is None or df.empty:
                return df
            df = df.drop(columns=["turnover", "outstanding_share"], errors="ignore")
            return df.rename(columns={"amount": "turnover"})

    try:
        return _retry(em)
    except Exception:
        return _retry(sina, tries=3)


def _ymd(x):
    return str(x).replace("-", "")[:8]


def upsert_bars(db, sym, df, etf, prev_close_seed=None):
    """写 stock_market(与 loader 字段一致);返回写入条数。"""
    if df is None or df.empty:
        return 0
    df = df.sort_values("date").reset_index(drop=True)
    band = limit_band(sym, etf)
    prev_close = prev_close_seed
    ops = []
    for _, r in df.iterrows():
        dstr = _ymd(r["date"])
        close = float(r["close"])
        pc = float(prev_close) if prev_close is not None else float(r["open"])
        ops.append(pymongo.UpdateOne(
            {"symbol": sym, "date": dstr},
            {"$set": {
                "symbol": sym, "code": bare(sym), "date": dstr, "trade_date": int(dstr),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": close,
                "volume": float(r["volume"]),
                "turnover": float(r.get("turnover", 0) or 0),
                "preclose": pc, "pre_close": pc,
                "limit_up": round(pc * (1 + band), 3 if etf else 2),
                "limit_down": round(pc * (1 - band), 3 if etf else 2),
                "trade_status": "交易"}}, upsert=True))
        prev_close = close
    if ops:
        db["stock_market"].bulk_write(ops, ordered=False)
    return len(ops)


def incremental_update(db, sym, etf=None, today=None):
    """增量更新单标的;返回 (新增条数, 说明)。带复权漂移检测→自动全量重拉。"""
    if etf is None:
        etf = is_etf(sym)
    today = today or datetime.date.today().strftime("%Y%m%d")
    col = db["stock_market"]
    tail = list(col.find({"symbol": sym}, {"_id": 0, "date": 1, "close": 1})
                .sort("date", -1).limit(10))
    if not tail:
        df = fetch_daily(sym, FULL_START, today, etf)
        return upsert_bars(db, sym, df, etf), "首拉(库里无历史)"
    tail.reverse()
    last_date = tail[-1]["date"]
    if last_date >= today:
        return 0, "已是最新"
    df = fetch_daily(sym, tail[0]["date"], today, etf)
    if df is None or df.empty:
        return 0, "源无新数据"
    df = df.sort_values("date").reset_index(drop=True)
    got = {_ymd(r["date"]): float(r["close"]) for _, r in df.iterrows()}
    for d in tail:                          # 重叠区比对:检测前复权漂移
        if d["date"] in got and d["close"]:
            if abs(got[d["date"]] / float(d["close"]) - 1) > 0.002:
                full = fetch_daily(sym, FULL_START, today, etf)
                n = upsert_bars(db, sym, full, etf)
                return n, "检测到复权漂移(分红/拆分),已全量重拉"
    new_df = df[df["date"].map(_ymd) > last_date]
    n = upsert_bars(db, sym, new_df, etf, prev_close_seed=float(tail[-1]["close"]))
    return n, f"增量 {n} 天" if n else "无新交易日"


def _lookup_name(b, etf, us=False):
    if us:
        try:
            import yfinance as yf
            info = yf.Ticker(b).info
            return info.get("shortName") or info.get("longName") or b
        except Exception:
            return b
    try:
        import akshare as ak
        if etf:
            df = ak.fund_name_em()
            row = df[df[df.columns[0]].astype(str) == b]
            if len(row):
                return str(row.iloc[0][df.columns[1]])
        else:
            df = ak.stock_info_a_code_name()
            row = df[df["code"].astype(str) == b]
            if len(row):
                return str(row.iloc[0]["name"])
    except Exception:
        pass
    return b


def add_symbol(db, code):
    """前端新增自选:归一化→判类型→全量拉取→写行情/名称/自选表。返回结果 dict。
    支持:A股6位数字代码(600519/512480)、美股字母 ticker(AAPL/QQQ 或 AAPL.US)。"""
    sym = norm_symbol(code)
    us = is_us(sym)
    if not sym or (not us and (len(bare(sym)) != 6 or not bare(sym).isdigit())) \
            or (us and not bare(sym).replace("-", "").isalpha()):
        return {"ok": False, "error": f"代码不合法:{code}"}
    etf = is_etf(sym) and not us
    today = datetime.date.today().strftime("%Y%m%d")
    have = db["stock_market"].count_documents({"symbol": sym}, limit=1)
    if have:
        n, note = incremental_update(db, sym, etf, today)
        note = f"已在库中,{note}"
    else:
        try:
            df = fetch_daily(sym, FULL_START, today, etf)
        except Exception as e:
            return {"ok": False, "error": f"拉取失败:{e}"}
        n = upsert_bars(db, sym, df, etf)
        if not n:
            return {"ok": False, "error": "数据源返回空(代码是否正确?)"}
        note = f"全量拉取 {n} 条"
    name = _lookup_name(bare(sym), etf, us=us)
    db["stock_info_new"].update_one(
        {"symbol": sym},
        {"$set": {"symbol": sym, "code": bare(sym), "name": name, "type": 0}}, upsert=True)
    db[WATCH_COL].update_one(
        {"symbol": sym},
        {"$set": {"symbol": sym, "name": name,
                  "asset_type": "us" if us else ("etf" if etf else "stock"),
                  "source": "manual",
                  "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}}, upsert=True)
    return {"ok": True, "symbol": sym, "name": name, "etf": etf, "us": us, "bars": n, "note": note}


def remove_symbol(db, sym, protected=()):
    """移除自选:删自选表;若非保护标的,连行情/名称一起删(数据可随时重拉)。"""
    sym = norm_symbol(sym)
    if sym in protected:
        db[WATCH_COL].delete_one({"symbol": sym})
        return {"ok": True, "symbol": sym, "note": "已移出自选;此标的被现役引擎使用,行情保留"}
    db[WATCH_COL].delete_one({"symbol": sym})
    n = db["stock_market"].delete_many({"symbol": sym}).deleted_count
    db["stock_info_new"].delete_one({"symbol": sym})
    return {"ok": True, "symbol": sym, "note": f"已移除(连同 {n} 条行情)"}
