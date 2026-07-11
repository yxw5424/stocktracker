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
import time
import datetime
import urllib.parse

import pymongo


def _retry(fn, what, tries=3, delay=2):
    """带退避重试;全部失败抛最后一个异常。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < tries - 1:
                print(f"[retry] {what} 第{i + 1}次失败({e}),{delay}s 后重试")
                time.sleep(delay)
                delay *= 2
    raise last


def norm_symbol(code: str) -> str:
    """000001 -> 000001.SZ;600000 -> 600000.SH;已带后缀则原样。"""
    code = code.strip().upper()
    if not code:
        return ""
    if "." in code:
        return code
    if code[0] == "8" or code[:3] in ("920", "430"):   # 北交所优先判(920 别被 9→SH 截胡)
        return code + ".BJ"
    if code[0] in ("5", "6", "9"):   # 5xxxxx=沪市ETF/基金, 6=沪A, 9=沪B
        return code + ".SH"
    return code + ".SZ"


def bare(sym: str) -> str:
    return sym.split(".")[0]


# 已知 20% 涨跌幅的非588 ETF(跟踪创业板类指数);可用 ETF20 环境变量追加
_ETF20_BUILTIN = {"159915", "159952", "159948", "159957", "159966", "159967"}


def limit_band(sym: str, is_etf: bool) -> float:
    """按板块规则给涨跌幅带宽(回测引擎会用 limit_up/down 拒单和截价,写错会造成
    假拒单/假优价成交)。688/300=20%,北交所=30%,588(科创ETF)/创业板类ETF=20%,
    其余 10%。追加名单:ETF20=159781,...(逗号分隔)"""
    b = bare(sym)
    if is_etf:
        etf20 = _ETF20_BUILTIN | {x.strip() for x in os.getenv("ETF20", "").split(",") if x.strip()}
        if b.startswith("588") or b in etf20:
            return 0.20
        return 0.10
    if sym.endswith(".BJ"):
        return 0.30
    if b.startswith(("688", "689", "300", "301", "302")):
        return 0.20
    return 0.10


def read_watchlist_file(path: str) -> list:
    """读单个名单文件(一行一个代码,#注释),返回归一化后的去重列表。"""
    out, seen = [], set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                s = norm_symbol(line) if line else ""
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
    return out


# LOAD_ALL=1 时按此清单全量灌入:(名单文件, 是否ETF)。以后加新名单在这里登记。
_ALL_LISTS = [
    ("/data/watchlist.txt", False),
    ("/data/watchlist-kechuang-semi.txt", False),
    ("/data/watchlist-etf.txt", True),
    ("/data/watchlist-etf-broad.txt", True),
    ("/data/watchlist-smartbeta.txt", True),
]


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


def _fetch_stock_daily(sym, start, end):
    """拉个股日线,返回标准化 DataFrame(volume 单位:股, turnover 单位:元)。
    源1: 东财(stock_zh_a_hist) → 源2: 新浪(stock_zh_a_daily)。"""
    import akshare as ak

    b = bare(sym)

    def em():
        df = ak.stock_zh_a_hist(symbol=b, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return df
        df = df.rename(columns=_COLMAP)
        df["volume"] = df["volume"].astype(float) * 100.0   # 东财单位:手 → 股
        return df

    def sina():
        pre = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[sym.rsplit(".", 1)[1]]
        df = ak.stock_zh_a_daily(symbol=f"{pre}{b}", start_date=start,
                                 end_date=end, adjust="qfq")
        if df is None or df.empty:
            return df
        # 新浪列名已是英文,volume 单位:股。注意它自带 turnover 列(是换手率!),
        # 先删掉再把 amount(成交额)改名成 turnover,否则两列同名取值变 Series。
        df = df.drop(columns=["turnover", "outstanding_share"], errors="ignore")
        df = df.rename(columns={"amount": "turnover"})
        return df

    try:
        return _retry(em, f"东财日线 {sym}", tries=2)
    except Exception as e:
        print(f"[daily] 东财不通({e}),换新浪源:{sym}")
        return _retry(sina, f"新浪日线 {sym}", tries=3)


def _fetch_etf_daily(sym, start, end):
    """拉 ETF 日线,东财(fund_etf_hist_em)→ 新浪(fund_etf_hist_sina)。volume 单位统一为股。"""
    import akshare as ak

    b = bare(sym)

    def em():
        df = ak.fund_etf_hist_em(symbol=b, period="daily",
                                 start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return df
        df = df.rename(columns=_COLMAP)
        df["volume"] = df["volume"].astype(float) * 100.0   # 东财:手 → 股
        return df

    def sina():
        pre = {"SH": "sh", "SZ": "sz"}.get(sym.rsplit(".", 1)[1], "sh")
        df = ak.fund_etf_hist_sina(symbol=f"{pre}{b}")       # 全历史,英文列 volume 单位:股
        if df is None or df.empty:
            return df
        df["date"] = df["date"].astype(str)
        d0 = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        d1 = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
        df = df[(df["date"] >= d0) & (df["date"] <= d1)].reset_index(drop=True)
        return df

    try:
        return _retry(em, f"东财ETF {sym}", tries=2)
    except Exception as e:
        print(f"[daily] 东财ETF不通({e}),换新浪源:{sym}")
        return _retry(sina, f"新浪ETF {sym}", tries=3)


def load_daily(db, symbols, start, end, is_etf=None):
    col = db["stock_market"]
    if is_etf is None:
        is_etf = os.getenv("ASSET_TYPE", "stock").lower() == "etf"
    fetch = _fetch_etf_daily if is_etf else _fetch_stock_daily
    total = 0
    for sym in symbols:
        b = bare(sym)
        try:
            df = fetch(sym, start, end)
        except Exception as e:
            print(f"[daily][SKIP] {sym}: {e}")
            continue
        if df is None or df.empty:
            print(f"[daily][EMPTY] {sym}")
            continue
        df = df.sort_values("date").reset_index(drop=True)
        band = limit_band(sym, is_etf)
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
                "volume": float(r["volume"]),   # 已在 _fetch_stock_daily 里统一为「股」
                "turnover": float(r.get("turnover", 0) or 0),
                "preclose": pc, "pre_close": pc,
                "limit_up": round(pc * (1 + band), 3 if is_etf else 2),
                "limit_down": round(pc * (1 - band), 3 if is_etf else 2),
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


# 基准指数：回测引擎把下拉选项映射成这些代码(见平台 main_workflow_stock.py 的 symbol_map，
# 中证500/1000 的代码是平台自定义的)，按 stock_info type=1 从 index_daily_price 集合读。
# 缺了它，回测收尾算基准收益时报 'NoneType' object has no attribute 'last'。
_BENCHMARKS = [
    # (引擎期望的symbol, 名称, 东财代码, 新浪代码)
    ("000001.SH", "上证指数", "000001", "sh000001"),
    ("000300.SH", "沪深300", "000300", "sh000300"),
    ("000500.SH", "中证500", "000905", "sh000905"),
    ("001000.SH", "中证1000", "000852", "sh000852"),
]


def _fetch_index_daily(name, ak_code, sina_sym, start, end):
    """拉指数日线,东财不通换新浪。返回列名标准化后的 DataFrame。"""
    import akshare as ak
    import pandas as pd

    def em():
        df = ak.index_zh_a_hist(symbol=ak_code, period="daily",
                                start_date=start, end_date=end)
        if df is None or df.empty:
            return df
        return df.rename(columns=_COLMAP)

    def sina():
        df = ak.stock_zh_index_daily(symbol=sina_sym)   # 全历史,英文列名
        if df is None or df.empty:
            return df
        df["date"] = df["date"].astype(str)
        d0 = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        d1 = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
        return df[(df["date"] >= d0) & (df["date"] <= d1)].reset_index(drop=True)

    try:
        return _retry(em, f"东财指数 {name}", tries=2)
    except Exception as e:
        print(f"[index] 东财不通({e}),换新浪源:{name}")
        return _retry(sina, f"新浪指数 {name}", tries=3)


def load_benchmarks(db, start, end):
    info_col, bar_col = db["stock_info_new"], db["index_daily_price"]
    for sym, name, ak_code, sina_sym in _BENCHMARKS:
        info_col.update_one(
            {"symbol": sym},
            {"$set": {"symbol": sym, "code": bare(sym), "name": name, "type": 1}},
            upsert=True)
        try:
            df = _fetch_index_daily(name, ak_code, sina_sym, start, end)
        except Exception as e:
            print(f"[index][SKIP] {name}({sym}): {e}")
            continue
        if df is None or df.empty:
            print(f"[index][EMPTY] {name}({sym})")
            continue
        df = df.sort_values("date").reset_index(drop=True)
        prev_close, ops = None, []
        for _, r in df.iterrows():
            dstr = _ymd(r["date"])
            close = float(r["close"])
            pc = float(prev_close) if prev_close is not None else float(r["open"])
            ops.append(pymongo.UpdateOne(
                {"symbol": sym, "date": dstr},
                {"$set": {
                    "symbol": sym, "code": bare(sym), "date": dstr,
                    "trade_date": int(dstr),
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": close,
                    "volume": float(r.get("volume", 0) or 0),
                    "turnover": float(r.get("turnover", 0) or 0),
                    "preclose": pc, "pre_close": pc,
                    "trade_status": "交易",
                }}, upsert=True))
            prev_close = close
        if ops:
            bar_col.bulk_write(ops, ordered=False)
            print(f"[index][OK] {name}({sym}): {len(ops)} 条")
    bar_col.create_index([("symbol", 1), ("date", 1)])


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
    print("[db索引] OK")


def main():
    load_all = os.getenv("LOAD_ALL") == "1"
    end = os.getenv("END_DATE") or datetime.date.today().strftime("%Y%m%d")

    if load_all:
        # 全量模式:四份名单一次灌完(股票名单走股票源,ETF名单走ETF源,跨名单去重)。
        # 以后更新数据也用这一条命令,不再挑名单/挑类型。
        start = os.getenv("START_DATE", "20210101")
        batches, seen = [], set()
        for path, is_etf in _ALL_LISTS:
            syms = [s for s in read_watchlist_file(path) if s not in seen]
            seen.update(syms)
            if syms:
                batches.append((path, is_etf, syms))
            else:
                print(f"[全量] {path} 缺失或为空,跳过")
        if not batches:
            print("全量模式下四份名单都为空,检查 volume 挂载。")
            sys.exit(1)
        all_syms = [s for _, _, syms in batches for s in syms]
        print(f"[全量] 共 {len(all_syms)} 只(股票+ETF),区间 {start} ~ {end}")
        # 前端「自选管理」加的标的存在 Mongo(user_watchlist),也并入全量更新
        try:
            wdb = mongo_db()
            manual = [(w["symbol"], w.get("asset_type") == "etf")
                      for w in wdb["user_watchlist"].find({}, {"symbol": 1, "asset_type": 1})
                      if w.get("symbol") and w["symbol"] not in seen]
            for sym, etf in manual:
                batches.append((f"前端自选:{sym}", etf, [sym]))
                all_syms.append(sym)
            if manual:
                print(f"[全量] 另有前端自选 {len(manual)} 只并入")
        except Exception as e:
            print(f"[全量][WARN] 读取前端自选失败(跳过):{e}")
    else:
        symbols = read_watchlist()
        if not symbols:
            print("没有自选股。请在 docker/watchlist.txt 里一行一个填代码,或设 WATCHLIST 环境变量。")
            sys.exit(1)
        start = os.getenv("START_DATE", "20240101")
        print(f"自选股 {len(symbols)} 只:{', '.join(symbols)}")
        print(f"区间 {start} ~ {end}")

    db = mongo_db()
    # 清理早期版本的错后缀数据:5开头是沪市ETF/基金,深市不存在 5xxxxx,
    # 之前被错存成 .SZ 的行情/信息全部删除(正确数据会以 .SH 重新灌入)。
    for coll in ("stock_market", "stock_info_new"):
        n = db[coll].delete_many({"symbol": {"$regex": r"^5\d{5}\.SZ$"}}).deleted_count
        if n:
            print(f"[清理] {coll} 删除错后缀(5xxxxx.SZ)文档 {n} 条")
    load_calendar(db, end)
    load_benchmarks(db, start, end)
    if load_all:
        load_info(db, all_syms)
        for path, is_etf, syms in batches:
            print(f"[全量] {path}({'ETF' if is_etf else '股票'}源,{len(syms)} 只)")
            load_daily(db, syms, start, end, is_etf=is_etf)
        if os.getenv("LOAD_MINUTE") == "1":
            print("[全量] LOAD_MINUTE 在全量模式下忽略(量太大,单独按名单跑)")
    else:
        load_info(db, symbols)
        load_daily(db, symbols, start, end)
        if os.getenv("LOAD_MINUTE") == "1":
            load_minute(db, symbols, start, end)
    ensure_indexes(db)
    print("完成。记得重启 quantflow 让新数据生效:docker compose restart quantflow")


if __name__ == "__main__":
    main()
