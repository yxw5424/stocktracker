# -*- coding: utf-8 -*-
"""
AI 信号复核官 —— 「规则出信号 → agent 收资料复核 → 决定执行/否决 → 事后总结」每日闭环。

流程(每个交易日收盘后跑一次):
  1. 信号   用趋势波段规则扫自选股(数据读 Mongo 的 stock_market),对照虚拟持仓账本
            得出今天的 买入/卖出 信号。
  2. 复核   对每个信号收集当时的事实:主力/散户资金流向、近几日新闻标题、
            基准指数趋势、个股近期表现 —— 打包发给 LLM。
  3. 决定   LLM 只有三种权力:执行 / 半仓 / 否决,必须给理由。
            决定 + 资料快照写入 Mongo(ai_review_decisions),虚拟持仓账本同步更新。
  4. 总结   回看 EVAL_DAYS 个交易日前的决定,算对错(否决的买入躲掉跌了吗?
            放行的赚了吗?),让 LLM 写复盘,存为「经验」(ai_review_lessons);
            最近经验会喂回给之后的每次决策 —— 记忆闭环。
输出:reports/review_YYYYMMDD.md 决策报告。

安全边界:纯纸面(虚拟持仓),不接实盘;AI 无反向开仓权。
环境变量:MONGO_URI/MONGO_DB 等同 loader;DEEPSEEK_API_KEY 或 ANTHROPIC_API_KEY;
         PROVIDER=deepseek|claude|mock;EVAL_DAYS=5;REPORT_DIR=/reports
"""
import os
import json
import datetime
import urllib.parse

import pymongo
import pandas as pd

PROVIDER = os.getenv("PROVIDER", "deepseek").lower()
FULL_AUTH = os.getenv("FULL_AUTH", "1") == "1"   # 全权模式:agent 可主动发起买卖(纸面)
EVAL_DAYS = int(os.getenv("EVAL_DAYS", "5"))
REPORT_DIR = os.getenv("REPORT_DIR", "/reports")
BENCH = "000300.SH"     # 复核用基准:沪深300
MAX_POS = 5             # 虚拟账本最多同时持有
COL_POS, COL_DEC, COL_LES = "ai_review_positions", "ai_review_decisions", "ai_review_lessons"

# 华泰模拟盘对接:配了 HT_APIKEY 就把每笔"执行/半仓"的决定同时发到华泰模拟盘(参赛)。
# HTSC_LIVE=1 才真下单,否则干跑(报告里标 [干跑])。每股下单量:
ORDER_LOTS = int(os.getenv("HTSC_ORDER_LOTS", "100"))   # 每笔下单股数(ETF 100 起、100 递增)
try:
    from htsc_broker import HTSCBroker, split_symbol
    _BROKER = HTSCBroker() if os.getenv("HT_APIKEY") else None
except Exception:
    _BROKER = None


_ACCT_CACHE = {"total": None, "tried": False}


def _find_asset_number(obj):
    """在余额响应里递归找『总资产』类字段(键名匹配+数值合理)。"""
    KEYS = ("totalasset", "total_asset", "netasset", "net_asset", "totalvalue",
            "total_value", "asset", "总资产", "净资产", "总市值")
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower().replace("-", "_")
            if isinstance(v, (int, float)) and v > 1000 and any(t in kl for t in KEYS):
                return float(v)
        for v in obj.values():
            got = _find_asset_number(v)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_asset_number(v)
            if got:
                return got
    return None


def _account_total():
    """查华泰账户总资产(只读,干跑模式也真查);查不到返回 None,只查一次。"""
    if _ACCT_CACHE["tried"]:
        return _ACCT_CACHE["total"]
    _ACCT_CACHE["tried"] = True
    try:
        resp = _BROKER.account_balance()
        _ACCT_CACHE["total"] = _find_asset_number(resp)
    except Exception:
        _ACCT_CACHE["total"] = None
    return _ACCT_CACHE["total"]


def _order_qty(price):
    """下单股数:①按账户总资产比例(HTSC_BUDGET_PCT,默认31%≈等权3只);
    ②固定金额预算(HTSC_ORDER_BUDGET 元);③兜底固定手数 HTSC_ORDER_LOTS。"""
    price = float(price)
    pct = float(os.getenv("HTSC_BUDGET_PCT", "0.31"))
    if pct > 0 and _BROKER is not None:
        total = _account_total()
        if total:
            qty = int(total * pct / price / 100) * 100
            if qty >= 100:
                return qty, f"总资产{total:,.0f}×{pct:.0%}"
    budget = float(os.getenv("HTSC_ORDER_BUDGET", "0"))
    if budget > 0:
        qty = int(budget / price / 100) * 100
        if qty >= 100:
            return qty, f"固定预算{budget:,.0f}元"
    return ORDER_LOTS, "固定手数兜底"


def broker_order(side, symbol, price, log_lines, half=False, qty=None):
    """把决定映射成华泰模拟盘下单;side 含'买'→buy,含'卖'→sell。返回结果字符串。
    默认按账户资产比例定量(等权);配置引擎(alloc_p1)会直接传入 qty。
    AI 判「半仓」时减半;卖出由后端按持仓校验。"""
    if _BROKER is None:
        return ""
    code, ex = split_symbol(symbol)
    direction = "buy" if "买" in side else "sell"
    if qty is not None:
        qty, how = int(qty), "配置引擎定量"
        if half and qty > 100:   # 配置引擎的半仓=调仓量减半,买卖同理(与账本一致)
            qty = max(100, int(qty / 2 / 100) * 100)
            how += ",AI判半仓已减半"
            half = False
    else:
        qty, how = _order_qty(price)
    if half and direction == "buy" and qty > 100:
        qty = max(100, int(qty / 2 / 100) * 100)
        how += ",AI判半仓已减半"
    r = _BROKER.submit_order(direction, code, ex, qty,
                             order_type="limit", price=round(float(price), 3))
    if r.get("dry_run"):
        return (f"  - 🧪 华泰[干跑] 将{('买入' if direction=='buy' else '卖出')} "
                f"{code}.{ex} {qty}股({how}) @ {price}")
    if r.get("ok") is False:
        return f"  - ⚠️ 华泰下单失败:{r.get('error', {}).get('message', r)}"
    return f"  - 📈 华泰[实盘模拟]已提交 {direction} {code}.{ex} {qty}股({how}):{json.dumps(r, ensure_ascii=False)[:200]}"


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def mongo_db():
    host = os.getenv("MONGO_URI", "mongo:27017")
    user, pwd = os.getenv("MONGO_USER", ""), os.getenv("MONGO_PASSWORD", "")
    authdb = os.getenv("MONGO_AUTH_DB", "admin")
    auth = f"{user}:{urllib.parse.quote_plus(pwd)}@" if user else ""
    cli = pymongo.MongoClient(f"mongodb://{auth}{host}/{authdb}", serverSelectionTimeoutMS=30000)
    cli.admin.command("ping")
    return cli[os.getenv("MONGO_DB", "panda")]


def _norm(code):
    """代码归一化:5/6/9→SH,8/920/430→BJ(优先),其余→SZ;已带后缀原样。"""
    code = code.strip().upper()
    if not code or "." in code:
        return code
    if code[0] == "8" or code[:3] in ("920", "430"):
        return code + ".BJ"
    return code + (".SH" if code[0] in "569" else ".SZ")


def read_watchlist():
    path = os.getenv("WATCHLIST_FILE", "/data/watchlist.txt")
    raw = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if line:
                raw.append(line)
    raw += [c for c in os.getenv("WATCHLIST", "").split(",") if c.strip()]
    out = [s for s in (_norm(c) for c in raw) if s]
    return list(dict.fromkeys(out))


def load_bars(db, symbols, days=200, collection="stock_market"):
    """从 Mongo 取近 N 条日线,返回 {symbol: DataFrame(date asc)}。"""
    out = {}
    for sym in symbols:
        docs = list(db[collection].find({"symbol": sym}, {"_id": 0})
                    .sort("date", -1).limit(days))
        if docs:
            out[sym] = pd.DataFrame(docs).sort_values("date").reset_index(drop=True)
    return out


# --------------------------------------------------------------------------- #
# ① 信号:趋势波段规则(与 flow 里的策略A同思路,纯 pandas 可测)
# --------------------------------------------------------------------------- #
def trend_signals(bars, positions):
    """bars: {sym: df};positions: {sym: {entry_price, high_since}}
    返回 [{symbol, side, price, rule}]。买入:MA20上穿MA60且同升、放量;
    卖出:破MA20 / 高点回撤10% / 止损8%。"""
    signals = []
    for sym, df in bars.items():
        if len(df) < 70:
            continue
        c = df["close"].astype(float)
        v = df["volume"].astype(float)
        ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
        vol20 = v.rolling(20).mean()
        i = len(df) - 1
        price = float(c.iloc[i])

        if sym in positions:
            p = positions[sym]
            high = max(float(p.get("high_since", price)), price)
            entry = float(p["entry_price"])
            if price < float(ma20.iloc[i]):
                signals.append({"symbol": sym, "side": "卖出", "price": price,
                                "rule": f"收盘 {price:.2f} 跌破20日线 {ma20.iloc[i]:.2f}"})
            elif price <= high * 0.90:
                signals.append({"symbol": sym, "side": "卖出", "price": price,
                                "rule": f"较持仓高点 {high:.2f} 回撤超10%"})
            elif price <= entry * 0.92:
                signals.append({"symbol": sym, "side": "卖出", "price": price,
                                "rule": f"跌破止损线(成本 {entry:.2f} 的92%)"})
        else:
            if len(positions) >= MAX_POS:
                continue
            cross = (ma20.iloc[i] > ma60.iloc[i]) and (ma20.iloc[i - 1] <= ma60.iloc[i - 1])
            rising = ma20.iloc[i] > ma20.iloc[i - 3] and ma60.iloc[i] > ma60.iloc[i - 3]
            heavy = v.iloc[i] > 1.5 * vol20.iloc[i]
            if cross and rising and heavy:
                signals.append({"symbol": sym, "side": "买入", "price": price,
                                "rule": "MA20上穿MA60且双线上行,当日放量>1.5倍20日均量"})
    return signals


# --------------------------------------------------------------------------- #
# ①b 信号引擎:冻结版 v1 动量轮动(strategies/a3_momentum_fast_exit_v1.py 的逐日版)
#    回测成绩:2022-2026H1 总收益319.8%/夏普1.781;2026H1 +24.0%/夏普2.65。
#    每日:持仓破20日线 或 较持仓高点回撤8% → 卖出;
#    周五:20日动量>0 且价>60日线,取前3;跌出前3调仓卖出;前3全部买入
#         (含已持仓者=v1的"给赢家加码"行为,实盘由资产比例定量近似)。
# --------------------------------------------------------------------------- #
SIGNAL_EXCLUDE = set(x.strip().upper() for x in
                     os.getenv("SIGNAL_EXCLUDE", "511990.SH").split(",") if x.strip())


def momentum_v1_signals(bars, positions, trade_date):
    """bars: {sym: df};positions: {sym: {entry_price, high_since}};trade_date: 'YYYYMMDD'。
    返回 [{symbol, side, price, rule}]。忠实还原冻结版 v1(见 strategies/ 目录)。"""
    signals = []
    universe = [s for s in bars if s not in SIGNAL_EXCLUDE]
    prices, hists = {}, {}
    for s in universe:
        df = bars[s]
        if df is None or len(df) < 1:
            continue
        h = df["close"].astype(float).tolist()[-250:]
        if not h:
            continue
        prices[s], hists[s] = h[-1], h

    # 每日卖出检查(与 v1 相同:先破线,再回撤;回撤基准是账本里的 high_since)
    for s in list(positions):
        if s not in prices or len(hists.get(s, [])) < 20:
            continue
        price = prices[s]
        ma20 = sum(hists[s][-20:]) / 20
        if price < ma20:
            signals.append({"symbol": s, "side": "卖出", "price": price,
                            "rule": f"[v1每日风控] 收盘 {price:.3f} 跌破20日线 {ma20:.3f}"})
            continue
        high = max(float(positions[s].get("high_since", price)), price)
        if high > 0 and (high - price) / high >= 0.08:
            signals.append({"symbol": s, "side": "卖出", "price": price,
                            "rule": f"[v1每日风控] 较持仓高点 {high:.3f} 回撤≥8%"})

    # 周五调仓(以数据日为准,不看跑脚本的自然日)
    try:
        weekday = datetime.datetime.strptime(str(trade_date), "%Y%m%d").weekday()
    except Exception:
        weekday = -1
    if weekday != 4:
        return signals

    candidates = []
    for s in universe:
        h = hists.get(s, [])
        if s not in prices or len(h) < 60:
            continue
        p20 = h[-20]
        if not p20:
            continue
        mom = prices[s] / p20 - 1
        if mom <= 0:
            continue
        if prices[s] <= sum(h[-60:]) / 60:
            continue
        candidates.append((s, mom))
    candidates.sort(key=lambda x: x[1], reverse=True)
    top3 = [c[0] for c in candidates[:3]]

    sold = {sig["symbol"] for sig in signals if sig["side"] == "卖出"}
    for s in list(positions):
        if s not in top3 and s not in sold and s in prices:
            signals.append({"symbol": s, "side": "卖出", "price": prices[s],
                            "rule": "[v1周五调仓] 跌出动量前3"})
    for rank, s in enumerate(top3, 1):
        if s in sold:
            continue
        tag = "加码(v1对领先者的行为)" if s in positions else "新开仓"
        signals.append({"symbol": s, "side": "买入", "price": prices[s],
                        "rule": f"[v1周五调仓] 20日动量第{rank}名,{tag}"})
    return signals


def _alloc_weights():
    """解析 ALLOC_WEIGHTS(如 '510300.SH:0.40,511260.SH:0.40,518880.SH:0.20')。"""
    weights = {}
    for part in os.getenv("ALLOC_WEIGHTS",
                          "510300.SH:0.40,511260.SH:0.40,518880.SH:0.20").split(","):
        try:
            s, w = part.rsplit(":", 1)
            weights[_norm(s.strip())] = float(w)
        except ValueError:
            continue
    return weights


def alloc_signals(bars, positions, trade_date):
    """P1 配置引擎(四轮走查唯一过线者,见 A-SHARE-STRATEGY-RESEARCH.md):
    股/债/金按 ALLOC_WEIGHTS 月度再平衡。只在每月首个交易日出信号(以数据日为准);
    偏离目标权重<2%总资产不动。信号带 qty(股数),AI 复核的角色=审偏离,
    不再是审动量信号。账本(ai_review_positions)在此引擎下记录 qty 字段。"""
    weights = _alloc_weights()
    ref = next((bars[s] for s in weights if s in bars and len(bars[s]) >= 2), None)
    if ref is None:
        return []
    d_today, d_prev = str(ref["date"].iloc[-1]), str(ref["date"].iloc[-2])
    if d_today[:6] == d_prev[:6]:
        return []   # 不是本月首个交易日
    total = _account_total() or float(os.getenv("PAPER_TOTAL", "1000000"))
    sigs = []
    for s, w in weights.items():
        if s not in bars or bars[s] is None or not len(bars[s]):
            continue
        price = float(bars[s]["close"].iloc[-1])
        cur_qty = float((positions.get(s) or {}).get("qty", 0) or 0)
        delta_val = total * w - cur_qty * price
        if abs(delta_val) < total * 0.02:
            continue
        qty = int(abs(delta_val) / price / 100) * 100
        if qty < 100:
            continue
        sigs.append({"symbol": s, "side": "买入" if delta_val > 0 else "卖出",
                     "price": price, "qty": qty, "weight": w,
                     "rule": (f"[P1月度再平衡] 目标{w:.0%},当前偏离 {delta_val / total:+.1%}"
                              f"(按总资产 {total:,.0f} 计)")})
    return sigs


# --------------------------------------------------------------------------- #
# ② 复核资料收集(外部源全部允许失败,缺哪块就少哪块)
# --------------------------------------------------------------------------- #
def gather_context(db, sym, bars):
    ctx = {}
    df = bars.get(sym)
    if df is not None and len(df) >= 21:
        c = df["close"].astype(float)
        ctx["近5日涨跌%"] = round((c.iloc[-1] / c.iloc[-6] - 1) * 100, 2)
        ctx["近20日涨跌%"] = round((c.iloc[-1] / c.iloc[-21] - 1) * 100, 2)
    bench = load_bars(db, [BENCH], days=70, collection="index_daily_price").get(BENCH)
    if bench is not None and len(bench) >= 61:
        bc = bench["close"].astype(float)
        ctx["基准沪深300近5日%"] = round((bc.iloc[-1] / bc.iloc[-6] - 1) * 100, 2)
        ctx["基准位于60日线上方"] = bool(bc.iloc[-1] > bc.rolling(60).mean().iloc[-1])
    code = sym.split(".")[0]
    try:
        import akshare as ak
        ff = ak.stock_individual_fund_flow(stock=code,
                                           market={"SH": "sh", "SZ": "sz"}.get(sym[-2:], "sh"))
        if ff is not None and not ff.empty:
            last = ff.iloc[-1]
            for col in ff.columns:
                if "主力净流入" in col and "占比" not in col:
                    ctx["当日主力净流入(万)"] = round(float(last[col]) / 1e4, 1)
                if "主力净流入" in col and "占比" in col:
                    ctx["主力净流入占比%"] = float(last[col])
    except Exception as e:
        ctx["资金流向"] = f"获取失败({type(e).__name__})"
    try:
        import akshare as ak
        news = ak.stock_news_em(symbol=code)
        if news is not None and not news.empty:
            title_col = [c for c in news.columns if "标题" in c]
            if title_col:
                ctx["近期新闻标题"] = news[title_col[0]].head(5).tolist()
    except Exception as e:
        ctx["新闻"] = f"获取失败({type(e).__name__})"
    return ctx


# --------------------------------------------------------------------------- #
# ③ LLM 决定(权力受限:执行/半仓/否决)
# --------------------------------------------------------------------------- #
_SYS = ("你是A股波段策略的风控复核官。规则策略已给出信号,你结合当下事实决定:"
        "「执行」「半仓」或「否决」。你没有反向操作权。原则:事实与信号方向冲突时"
        "(如买入信号但主力大幅流出+利空新闻+基准走弱)倾向否决或半仓;事实支持或中性时放行;"
        "卖出信号原则上不轻易否决(风控优先)。输出 JSON:"
        '{"decision":"执行|半仓|否决","reason":"三句话以内","confidence":0到1}')


def _decide_prompt(sig, ctx, lessons):
    parts = [f"【信号】{sig['side']} {sig['symbol']} @ {sig['price']}\n触发规则:{sig['rule']}",
             f"【当下事实】\n{json.dumps(ctx, ensure_ascii=False, indent=1)}"]
    if lessons:
        parts.append("【你过往决策的经验教训】\n" + "\n".join(f"- {x}" for x in lessons))
    parts.append("请给出决定(JSON)。")
    return "\n\n".join(parts)


def llm_decide(sig, ctx, lessons):
    prompt = _decide_prompt(sig, ctx, lessons)
    try:
        if PROVIDER == "claude" and os.getenv("ANTHROPIC_API_KEY"):
            import anthropic
            r = anthropic.Anthropic().messages.create(
                model="claude-opus-4-8", max_tokens=1024,
                system=_SYS, messages=[{"role": "user", "content": prompt}])
            txt = "".join(b.text for b in r.content if b.type == "text")
        elif os.getenv("DEEPSEEK_API_KEY"):
            import openai
            cli = openai.OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                                base_url="https://api.deepseek.com/v1")
            r = cli.chat.completions.create(
                model="deepseek-chat", temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": _SYS},
                          {"role": "user", "content": prompt}])
            txt = r.choices[0].message.content
        else:
            return {"decision": "执行", "reason": "无可用 LLM(mock):按规则默认执行", "confidence": 0.5}
        txt = txt.strip()
        if txt.startswith("```"):
            txt = txt.strip("`").lstrip("json").strip()
        d = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        if d.get("decision") not in ("执行", "半仓", "否决"):
            d["decision"] = "执行"
        return d
    except Exception as e:
        return {"decision": "执行", "reason": f"复核失败({type(e).__name__}),按规则默认执行", "confidence": 0.3}


# --------------------------------------------------------------------------- #
# ③b 全权模式:agent 纵览全局后可主动发起操作(纸面执行,同样记档、同样被评分)
# --------------------------------------------------------------------------- #
def agent_extra_actions(db, bars, positions, lessons):
    overview = {}
    for sym, df in bars.items():
        if len(df) >= 21:
            c = df["close"].astype(float)
            overview[sym] = {"近5日%": round((c.iloc[-1] / c.iloc[-6] - 1) * 100, 1),
                             "近20日%": round((c.iloc[-1] / c.iloc[-21] - 1) * 100, 1),
                             "持仓": sym in positions}
    pos_txt = [{"symbol": s, "成本": p["entry_price"],
                "现价": float(bars[s]["close"].iloc[-1]) if s in bars else None}
               for s, p in positions.items()]
    prompt = (
        "你是全权模式下的纸面盘操盘手。规则信号之外,你每天最多可以主动发起 2 笔操作"
        "(买入未持有的自选股 / 卖出当前持仓),也可以不动。原则:有明确事实依据才动,"
        "宁缺毋滥;持仓超过5只不再买。\n"
        f"【自选股概况】\n{json.dumps(overview, ensure_ascii=False)}\n"
        f"【当前持仓】\n{json.dumps(pos_txt, ensure_ascii=False)}\n"
        + (("【经验】\n" + "\n".join(f"- {x}" for x in lessons) + "\n") if lessons else "")
        + '输出 JSON:{"actions":[{"symbol":"600519.SH","action":"买入|卖出","reason":"一句话"}]},'
          '不动则 actions 为空数组。')
    try:
        if PROVIDER == "claude" and os.getenv("ANTHROPIC_API_KEY"):
            import anthropic
            r = anthropic.Anthropic().messages.create(
                model="claude-opus-4-8", max_tokens=1024,
                messages=[{"role": "user", "content": prompt}])
            txt = "".join(b.text for b in r.content if b.type == "text")
        elif os.getenv("DEEPSEEK_API_KEY"):
            import openai
            cli = openai.OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                                base_url="https://api.deepseek.com/v1")
            r = cli.chat.completions.create(
                model="deepseek-chat", temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}])
            txt = r.choices[0].message.content
        else:
            return []
        acts = json.loads(txt[txt.index("{"): txt.rindex("}") + 1]).get("actions", [])[:2]
        out = []
        for a in acts:
            sym, act = a.get("symbol", ""), a.get("action", "")
            if act == "买入" and sym in bars and sym not in positions and len(positions) < MAX_POS:
                out.append(a)
            elif act == "卖出" and sym in positions:
                out.append(a)
        return out
    except Exception as e:
        print(f"[全权模式] 主动决策失败(跳过):{e}")
        return []


# --------------------------------------------------------------------------- #
# ④ 总结:回看旧决定 → 经验
# --------------------------------------------------------------------------- #
def evaluate_old_decisions(db, bars, today):
    """给 ≥EVAL_DAYS 天前、未评估的决定打分,返回评估文本列表。"""
    outs = []
    for d in db[COL_DEC].find({"evaluated": {"$ne": True}}):
        df = bars.get(d["symbol"])
        if df is None:
            continue
        after = df[df["date"] > d["date"]]
        if len(after) < EVAL_DAYS:
            continue
        chg = (float(after["close"].iloc[EVAL_DAYS - 1]) / float(d["price"]) - 1) * 100
        if "买入" in d["side"]:
            good = (chg > 0) if d["decision"] in ("执行", "半仓") else (chg < 0)
            verdict = ("放行买入后涨" if chg > 0 else "放行买入后跌") if d["decision"] != "否决" \
                else ("否决买入,躲过下跌" if chg < 0 else "否决买入,错过上涨")
        else:
            good = (chg < 0) if d["decision"] in ("执行", "半仓") else (chg > 0)
            verdict = ("放行卖出,后续确实跌" if chg < 0 else "放行卖出,卖飞了") if d["decision"] != "否决" \
                else ("否决卖出,拿住了上涨" if chg > 0 else "否决卖出,多挨了跌")
        txt = (f"{d['date']} {d['side']} {d['symbol']} 决定={d['decision']}"
               f"(理由:{d.get('reason','')[:60]}) → {EVAL_DAYS}日后 {chg:+.1f}%,{verdict}")
        db[COL_DEC].update_one({"_id": d["_id"]},
                               {"$set": {"evaluated": True, "outcome_pct": round(chg, 2),
                                         "outcome_good": bool(good), "verdict": verdict}})
        outs.append(txt)
    return outs


def summarize_lessons(db, evals, today):
    if not evals:
        return None
    stat = list(db[COL_DEC].aggregate([
        {"$match": {"evaluated": True}},
        {"$group": {"_id": "$outcome_good", "n": {"$sum": 1}}}]))
    stat_txt = json.dumps({("正确" if s["_id"] else "错误"): s["n"] for s in stat}, ensure_ascii=False)
    prompt = ("以下是你(风控复核官)过往决定的最新评估结果:\n" + "\n".join(evals) +
              f"\n\n累计正确/错误分布:{stat_txt}\n"
              "请写不超过3条、每条一句话的可执行经验(如\"主力净流出超X时否决买入是对的\"),"
              "输出 JSON:{\"lessons\":[\"...\"]}")
    lessons = []
    try:
        if PROVIDER == "claude" and os.getenv("ANTHROPIC_API_KEY"):
            import anthropic
            r = anthropic.Anthropic().messages.create(
                model="claude-opus-4-8", max_tokens=1024,
                messages=[{"role": "user", "content": prompt}])
            txt = "".join(b.text for b in r.content if b.type == "text")
            lessons = json.loads(txt[txt.index("{"): txt.rindex("}") + 1]).get("lessons", [])
        elif os.getenv("DEEPSEEK_API_KEY"):
            import openai
            cli = openai.OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                                base_url="https://api.deepseek.com/v1")
            r = cli.chat.completions.create(
                model="deepseek-chat", temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}])
            lessons = json.loads(r.choices[0].message.content).get("lessons", [])
    except Exception:
        lessons = []
    doc = {"date": today, "evals": evals, "lessons": lessons}
    db[COL_LES].insert_one(doc)
    return doc


def recent_lessons(db, n=5):
    out = []
    for doc in db[COL_LES].find().sort("date", -1).limit(3):
        out += doc.get("lessons", [])
    return out[:n]


# --------------------------------------------------------------------------- #
# ⑤ 对账:实际成交 vs 决策价(回测/信号价与真实执行的差距,就在这里量化)
# --------------------------------------------------------------------------- #
COL_RECON, COL_EQ = "ai_review_recon", "ai_review_equity"


def _pick(d, *keys):
    """在成交记录 dict 里按候选键名取值(华泰字段名没有文档,防御式取)。"""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def _find_trade_lists(obj, out):
    """递归找出响应里『像成交记录列表』的部分:元素是含价格+数量字段的 dict。"""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and \
                _pick(obj[0], "price", "tradePrice", "dealPrice", "filledPrice") is not None:
            out.append(obj)
        else:
            for v in obj:
                _find_trade_lists(v, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            _find_trade_lists(v, out)


def reconcile(db, today):
    """①快照账户总资产(ai_review_equity);②拉华泰近14天成交,与最近的同向决策
    价对比算滑点(ai_review_recon,按成交ID去重);③返回报告行。全程只读。"""
    lines = []
    if _BROKER is None:
        return lines

    # ① 每日权益快照 —— 唯一无偏的成绩单,和回测预期曲线对着看
    try:
        total = _find_asset_number(_BROKER.account_balance())
        if total:
            db[COL_EQ].update_one({"date": today},
                                  {"$set": {"date": today, "total_asset": total}}, upsert=True)
            prev = db[COL_EQ].find_one({"date": {"$lt": today}}, sort=[("date", -1)])
            chg = (f"(较上一快照 {(total / prev['total_asset'] - 1) * 100:+.2f}%)"
                   if prev and prev.get("total_asset") else "")
            lines.append(f"- 账户总资产快照:{total:,.0f} 元 {chg}")
    except Exception as e:
        lines.append(f"- ⚠️ 权益快照失败:{e}")

    # ② 成交对账
    try:
        import datetime as _dt
        start = (_dt.date.today() - _dt.timedelta(days=14)).strftime("%Y-%m-%d")
        resp = _BROKER.trade_history(start=start)
        found = []
        _find_trade_lists(resp, found)
        trades = [t for lst in found for t in lst]
    except Exception as e:
        lines.append(f"- ⚠️ 拉取成交记录失败:{e}")
        trades = []

    new_rows = []
    for t in trades:
        code = str(_pick(t, "stockCode", "code", "symbol") or "")
        if not code:
            continue
        ex = str(_pick(t, "exchange", "market") or "")
        sym = f"{code.split('.')[0]}.{ex}" if ex in ("SH", "SZ", "BJ") else _norm(code)
        fill = _pick(t, "price", "tradePrice", "dealPrice", "filledPrice")
        qty = _pick(t, "quantity", "tradeQuantity", "dealQuantity", "filledQuantity")
        when = str(_pick(t, "tradeTime", "dealTime", "time", "createTime", "orderTime") or "")
        direction = str(_pick(t, "direction", "side", "bsFlag") or "").lower()
        is_buy = ("buy" in direction) or ("买" in direction)
        tid = str(_pick(t, "tradeId", "dealId", "id", "orderId") or f"{sym}|{when}|{fill}|{qty}")
        if fill is None or db[COL_RECON].find_one({"trade_id": tid}):
            continue
        fill = float(fill)
        # 找这笔成交对应的决策:同标的、同方向、成交前最近的一条「执行/半仓」
        side_pat = "买" if is_buy else "卖"
        dec = db[COL_DEC].find_one(
            {"symbol": sym, "side": {"$regex": side_pat},
             "decision": {"$in": ["执行", "半仓"]}},
            sort=[("date", -1)])
        row = {"trade_id": tid, "date": today, "symbol": sym,
               "side": "买入" if is_buy else "卖出", "fill_price": fill,
               "qty": float(qty) if qty is not None else None, "trade_time": when}
        if dec and dec.get("price"):
            dp = float(dec["price"])
            # 正数=比决策价吃亏(买贵了/卖便宜了),这就是回测(按决策价成交)与实盘的差
            slip = ((fill - dp) / dp if is_buy else (dp - fill) / dp) * 100
            row.update({"decision_date": dec["date"], "decision_price": dp,
                        "slippage_pct": round(slip, 3)})
        db[COL_RECON].insert_one(row)
        new_rows.append(row)

    for r in new_rows:
        if r.get("decision_price"):
            lines.append(f"- {r['side']} {r['symbol']}:决策价 {r['decision_price']} → "
                         f"实际成交 {r['fill_price']},滑点 {r['slippage_pct']:+.2f}%"
                         f"(正=吃亏)")
        else:
            lines.append(f"- {r['side']} {r['symbol']} 成交 @ {r['fill_price']}"
                         f"(没找到对应决策,可能是手动单)")

    # ③ 累计统计:滑点均值是把回测数字换算成实盘预期的折扣率
    agg = list(db[COL_RECON].aggregate([
        {"$match": {"slippage_pct": {"$ne": None}}},
        {"$group": {"_id": "$side", "n": {"$sum": 1},
                    "avg": {"$avg": "$slippage_pct"}}}]))
    if agg:
        stat = " · ".join(f"{a['_id']}{a['n']}笔 平均滑点{a['avg']:+.2f}%" for a in agg)
        lines.append(f"- 累计:{stat}")
    return lines


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    db = mongo_db()
    engine = os.getenv("SIGNAL_ENGINE", "alloc_p1").lower()
    watch = read_watchlist()
    if engine == "alloc_p1":
        # 配置引擎的标的必须在行情列表里,无论 watchlist 写没写它们
        watch = list(dict.fromkeys(list(_alloc_weights()) + watch))
    if not watch:
        print("自选股为空(watchlist.txt / WATCHLIST)")
        return
    bars = load_bars(db, watch)
    if not bars:
        print("Mongo 里没有行情,请先跑 loader")
        return
    today = max(df["date"].iloc[-1] for df in bars.values())
    print(f"复核日:{today},自选 {len(watch)} 只,有数据 {len(bars)} 只")

    positions = {p["symbol"]: p for p in db[COL_POS].find()}
    if engine == "alloc_p1":
        signals = alloc_signals(bars, positions, today)
    elif engine == "momentum_v1":
        signals = momentum_v1_signals(bars, positions, today)
    else:
        signals = trend_signals(bars, positions)
    print(f"信号引擎={engine},触发信号 {len(signals)} 条")

    # 更新持仓高点(用于回撤退出)
    for sym, p in positions.items():
        if sym in bars:
            price = float(bars[sym]["close"].iloc[-1])
            if price > float(p.get("high_since", 0)):
                db[COL_POS].update_one({"symbol": sym}, {"$set": {"high_since": price}})

    lessons = recent_lessons(db)
    lines = [f"# AI 信号复核报告 {today}", ""]
    if lessons:
        lines += ["## 当前记忆中的经验", *[f"- {x}" for x in lessons], ""]

    lines.append(f"## 今日信号与决定({len(signals)} 条)")
    if not signals:
        lines.append("今日无信号。")
    for sig in signals:
        ctx = gather_context(db, sig["symbol"], bars)
        d = llm_decide(sig, ctx, lessons)
        db[COL_DEC].insert_one({
            "date": today, "symbol": sig["symbol"], "side": sig["side"],
            "price": sig["price"], "rule": sig["rule"], "context": ctx,
            "decision": d["decision"], "reason": d.get("reason", ""),
            "confidence": d.get("confidence"), "evaluated": False})
        # 虚拟账本(纸面):执行/半仓的买入入账,执行的卖出出账
        if engine == "alloc_p1":
            # 配置引擎:账本按 qty 增减(半仓=执行一半的调仓量)
            if d["decision"] in ("执行", "半仓"):
                qexec = sig["qty"]
                if d["decision"] == "半仓" and qexec > 100:
                    qexec = max(100, int(qexec / 2 / 100) * 100)
                cur = float((positions.get(sig["symbol"]) or {}).get("qty", 0) or 0)
                new_qty = cur + qexec if sig["side"] == "买入" else cur - qexec
                if new_qty > 0:
                    db[COL_POS].update_one(
                        {"symbol": sig["symbol"]},
                        {"$set": {"symbol": sig["symbol"], "qty": new_qty,
                                  "entry_price": sig["price"], "entry_date": today,
                                  "high_since": sig["price"], "alloc": True,
                                  "weight": sig.get("weight")}}, upsert=True)
                else:
                    db[COL_POS].delete_one({"symbol": sig["symbol"]})
        elif sig["side"] == "买入" and d["decision"] in ("执行", "半仓"):
            db[COL_POS].update_one(
                {"symbol": sig["symbol"]},
                {"$set": {"symbol": sig["symbol"], "entry_price": sig["price"],
                          "entry_date": today, "high_since": sig["price"],
                          "half": d["decision"] == "半仓"}}, upsert=True)
        elif sig["side"] == "卖出" and d["decision"] in ("执行", "半仓"):
            db[COL_POS].delete_one({"symbol": sig["symbol"]})
        icon = {"执行": "✅", "半仓": "🌓", "否决": "⛔"}[d["decision"]]
        lines += [f"### {icon} {sig['side']} {sig['symbol']} @ {sig['price']}",
                  f"- 触发规则:{sig['rule']}",
                  f"- 关键事实:{json.dumps(ctx, ensure_ascii=False)[:400]}",
                  f"- **AI 决定:{d['decision']}**(信心 {d.get('confidence')}) —— {d.get('reason','')}"]
        if d["decision"] in ("执行", "半仓"):
            ob = broker_order(sig["side"], sig["symbol"], sig["price"], lines,
                              half=(d["decision"] == "半仓"), qty=sig.get("qty"))
            if ob:
                lines.append(ob)
        lines.append("")

    # 全权模式:agent 主动操作(纸面)
    if FULL_AUTH:
        positions = {p["symbol"]: p for p in db[COL_POS].find()}
        extras = agent_extra_actions(db, bars, positions, lessons)
        if extras:
            lines.append("## AI 主动操作(全权模式)")
        for a in extras:
            sym = a["symbol"]
            price = float(bars[sym]["close"].iloc[-1])
            side = f"AI主动{a['action']}"
            db[COL_DEC].insert_one({
                "date": today, "symbol": sym, "side": side, "price": price,
                "rule": "AI自主决策(全权模式)", "context": {},
                "decision": "执行", "reason": a.get("reason", ""),
                "confidence": None, "evaluated": False})
            if a["action"] == "买入":
                db[COL_POS].update_one({"symbol": sym},
                    {"$set": {"symbol": sym, "entry_price": price, "entry_date": today,
                              "high_since": price, "by_agent": True}}, upsert=True)
            else:
                db[COL_POS].delete_one({"symbol": sym})
            lines += [f"- 🤖 {a['action']} {sym} @ {price} —— {a.get('reason','')}"]
            ob = broker_order(a["action"], sym, price, lines)
            if ob:
                lines.append(ob)
        if extras:
            lines.append("")

    recon = reconcile(db, today)
    if recon:
        lines += ["## 对账:实际成交 vs 决策价", *recon, ""]

    evals = evaluate_old_decisions(db, bars, today)
    if evals:
        lines += ["## 对过往决定的复盘", *[f"- {x}" for x in evals], ""]
        summ = summarize_lessons(db, evals, today)
        if summ and summ.get("lessons"):
            lines += ["### 新沉淀的经验", *[f"- {x}" for x in summ["lessons"]], ""]

    pos_now = list(db[COL_POS].find({}, {"_id": 0}))
    lines += ["## 当前虚拟持仓", "(空仓)" if not pos_now else
              "\n".join(f"- {p['symbol']} 入场 {p['entry_price']} @ {p['entry_date']}"
                        f"{'(半仓)' if p.get('half') else ''}" for p in pos_now), ""]

    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"review_{today}.md")
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n报告已写入 {path}")


if __name__ == "__main__":
    main()
