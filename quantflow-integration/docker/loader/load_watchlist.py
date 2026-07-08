# -*- coding: utf-8 -*-
"""
把你自选股(watchlist)的 A 股行情灌进 QuantFlow 用的 MongoDB。
数据源:akshare(免费、无需 token)。

写入 DB `panda` 的这些集合(全部是平台回测引擎/数据层真正读的表):
  stock_market          日线 OHLCV(回测主数据)
  trade_calendar        交易日历 {nature_date:int, is_trade:1, exchange}   —— 引擎按它逐日推进
  trading_calendar_all  交易日历 {trading_date:str, sort_idx:int}          —— 算前/后一个交易日
  stock_info_new        股票名 {symbol, name, type:0}
  stock_market_ticket_zstd   分钟线(仅 LOAD_MINUTE=1 时,给看盘用)

配置全走环境变量(见 docker-compose 里的 loader 服务):
  MONGO_URI(默认 mongo:27017) MONGO_USER MONGO_PASSWORD MONGO_AUTH_DB MONGO_DB
  WATCHLIST      逗号分隔的股票代码(可带或不带交易所后缀:000001 或 000001.SZ)
  WATCHLIST_FILE 或直接放 /data/watchlist.txt(一行一个代码,# 开头为注释)
  START_DATE(默认 20240101) END_DATE(默认今天)
  LOAD_MINUTE=1  额外拉最近的 1 分钟线(较慢、量大;默认关)
"""
import os
import sys
import datetime
import urllib.parse

import pymongo


def norm_symbol(code: str) -> str:
    """000001 -> 000001.SZ;600000 -> 600000.SH;已带后缀则原样。"""
    code = code.strip().upper()
    if not code:
        return ""
    if "." in code:
        return code
    if code[0] in ("6", "9"):
        return code + ".SH"
    if code[0] == "8" or code[:3] in ("920", "430"):
        return code + ".BJ"
    return code + ".SZ"


def bare(sym: str) -> str:
    return sym.split(".")[0]


def read_watchlist() -> list:
    raw = []
    env = os.getenv("WATCHLIST", "")
    if env:
        raw += [x for x in env.replace("\n", ",").split(",")]
    path = os.getenv("WATCHLIST_FILE", "/data/watchlist.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()  # 剥掉行尾注释
                if line:
                    raw.append(line)
    seen, out = set(), []
    for c in raw:
        s = norm_symbol(c)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def mongo_db():
    host = os.getenv("MONGO_URI", "mongo:27017")
    user = os.getenv("MONGO_USER", "")
    pwd = os.getenv("MONGO_PASSWORD", "")
    authdb = os.getenv("MONGO_AUTH_DB", "admin")
    dbname = os.getenv("MONGO_DB", "panda")
    if user:
        uri = f"mongodb://{user}:{urllib.parse.quote_plus(pwd)}@{host}/{authdb}"
    else:
        uri = f"mongodb://{host}/{authdb}"
    cli = pymongo.MongoClient(uri, serverSelectionTimeoutMS=30000)
    cli.admin.command("ping")
    return cli[dbname]


# akshare 中文列名 -> 内部字段
_COLMAP = {
    "日期": "date", "时间": "date",
    "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "turnover",
}


def _ymd(x) -> str:
    s = str(x)
    return s.replace("-", "").replace("/", "").replace(" ", "").replace(":", "")[:8]


def _ymdhm(x) -> str:
    s = str(x).replace("-", "").replace("/", "").replace(":", "")
    return s.replace(" ", "")[:12]


def load_daily(db, symbols, start, end):
    import akshare as ak

    col = db["stock_market"]
    total = 0
    for sym in symbols:
        b = bare(sym)
        try:
            df = ak.stock_zh_a_hist(symbol=b, period="daily",
                                    start_date=start, end_date=end, adjust="qfq")
        except Exception as e:
            print(f"[daily][SKIP] {sym}: {e}")
            continue
        if df is None or df.empty:
            print(f"[daily][EMPTY] {sym}")
            continue
        df = df.rename(columns=_COLMAP)
        df = df.sort_values("date").reset_index(drop=True)
        prev_close = None
        ops = []
        for _, r in df.iterrows():
            dstr = _ymd(r["date"])
            close = float(r["close"])
            pc = float(prev_close) if prev_close is not None else float(r["open"])
            doc = {
                "symbol": sym, "code": b,
                "date": dstr, "trade_date": int(dstr),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": close,
                "volume": float(r["volume"]) * 100.0,   # akshare 单位是手,×100 转股
                "turnover": float(r.get("turnover", 0) or 0),
                "preclose": pc, "pre_close": pc,
                "limit_up": round(pc * 1.1, 2), "limit_down": round(pc * 0.9, 2),
                "trade_status": "交易",
            }
            ops.append(pymongo.UpdateOne(
                {"symbol": sym, "date": dstr}, {"$set": doc}, upsert=True))
            prev_close = close
        if ops:
            col.bulk_write(ops, ordered=False)
            total += len(ops)
            print(f"[daily][OK] {sym}: {len(ops)} 条")
    print(f"[daily] 合计写入 {total} 条")


def load_calendar(db, end):
    import akshare as ak

    try:
        cal = ak.tool_trade_date_hist_sina()
    except Exception as e:
        print(f"[calendar][ERROR] 拉交易日历失败:{e}")
        return
    col_key = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
    dates = sorted({_ymd(d) for d in cal[col_key]})
    dates = [d for d in dates if d <= end]
    tc, tca = db["trade_calendar"], db["trading_calendar_all"]
    ops_tc, ops_tca = [], []
    for idx, d in enumerate(dates):
        ops_tca.append(pymongo.UpdateOne(
            {"trading_date": d},
            {"$set": {"trading_date": d, "sort_idx": idx}}, upsert=True))
        for ex in ("SH", "SZ"):
            ops_tc.append(pymongo.UpdateOne(
                {"nature_date": int(d), "exchange": ex},
                {"$set": {"nature_date": int(d), "is_trade": 1, "exchange": ex}},
                upsert=True))
    if ops_tca:
        tca.bulk_write(ops_tca, ordered=False)
    if ops_tc:
        tc.bulk_write(ops_tc, ordered=False)
    print(f"[calendar] 交易日 {len(dates)} 天(至 {end})")


def load_info(db, symbols):
    import akshare as ak

    name_map = {}
    try:
        names = ak.stock_info_a_code_name()
        code_col = "code" if "code" in names.columns else names.columns[0]
        name_col = "name" if "name" in names.columns else names.columns[1]
        name_map = dict(zip(names[code_col].astype(str), names[name_col]))
    except Exception as e:
        print(f"[info][WARN] 拉股票名失败(用代码代替):{e}")
    col = db["stock_info_new"]
    ops = []
    for sym in symbols:
        b = bare(sym)
        ops.append(pymongo.UpdateOne(
            {"symbol": sym},
            {"$set": {"symbol": sym, "code": b, "name": name_map.get(b, b), "type": 0}},
            upsert=True))
    if ops:
        col.bulk_write(ops, ordered=False)
    print(f"[info] 写入 {len(ops)} 只股票信息")


def load_minute(db, symbols, start, end):
    import akshare as ak

    col = db["stock_market_ticket_zstd"]
    total = 0
    for sym in symbols:
        b = bare(sym)
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=b, period="1", adjust="qfq",
                start_date=f"{start[:4]}-{start[4:6]}-{start[6:8]} 09:30:00",
                end_date=f"{end[:4]}-{end[4:6]}-{end[6:8]} 15:00:00")
        except Exception as e:
            print(f"[min][SKIP] {sym}: {e}")
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns=_COLMAP)
        ops = []
        for _, r in df.iterrows():
            dt = str(r["date"])
            dstr = _ymdhm(dt)
            try:
                pydt = datetime.datetime.strptime(dt[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                pydt = None
            doc = {
                "symbol": sym, "date": dstr,
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r["volume"]) * 100.0,
                "total_turnover": float(r.get("turnover", 0) or 0),
                "num_trades": 0,
            }
            if pydt is not None:
                doc["datetime"] = pydt
            ops.append(pymongo.UpdateOne(
                {"symbol": sym, "date": dstr}, {"$set": doc}, upsert=True))
        if ops:
            col.bulk_write(ops, ordered=False)
            total += len(ops)
            print(f"[min][OK] {sym}: {len(ops)} 条")
    print(f"[min] 合计写入 {total} 条")


def ensure_indexes(db):
    db["stock_market"].create_index([("symbol", 1), ("date", 1)])
    db["stock_market"].create_index([("date", 1)])
    db["trade_calendar"].create_index([("nature_date", 1), ("exchange", 1)])
    db["trading_calendar_all"].create_index([("trading_date", 1)])
    db["trading_calendar_all"].create_index([("sort_idx", 1)])
    db["stock_info_new"].create_index([("symbol", 1)])
    db["stock_market_ticket_zstd"].create_index([("symbol", 1), ("date", 1)])
    print("[index] OK")


def main():
    symbols = read_watchlist()
    if not symbols:
        print("没有自选股。请在 docker/watchlist.txt 里一行一个填代码,或设 WATCHLIST 环境变量。")
        sys.exit(1)
    start = os.getenv("START_DATE", "20240101")
    end = os.getenv("END_DATE") or datetime.date.today().strftime("%Y%m%d")
    print(f"自选股 {len(symbols)} 只:{', '.join(symbols)}")
    print(f"区间 {start} ~ {end}")

    db = mongo_db()
    load_info(db, symbols)
    load_calendar(db, end)
    load_daily(db, symbols, start, end)
    if os.getenv("LOAD_MINUTE") == "1":
        load_minute(db, symbols, start, end)
    ensure_indexes(db)
    print("完成。现在可以在 /quantflow/ 里对这些股票跑回测了。")


if __name__ == "__main__":
    main()
