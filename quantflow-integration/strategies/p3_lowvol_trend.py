# -*- coding: utf-8 -*-
# P3 低波动×趋势 —— A股文献里比动量稳健的异象:低波动组合长期风险调整收益更好。
# 每月:在「昨收站上MA120」的标的里选 60日波动率最低的3只,等权持有;
# 已持仓只要仍站上MA120就不换(极低换手);日常仅在跌破MA120×0.98时离场。
# 无前视:全部信号用昨收,今开成交。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

POOL = ['510300.SH', '510500.SH', '159915.SZ', '588000.SH', '512760.SH', '515070.SH',
        '512690.SH', '512800.SH', '510880.SH', '515790.SH', '512660.SH', '512170.SH',
        '518880.SH', '511260.SH', '513100.SH', '510900.SH']
MA, VOLD, TOPN = 120, 60, 3

def initialize(context):
    context.hist = {s: [] for s in POOL}
    context.hold = set()
    context.prev_month = None

def _ma_ok(context, s):
    h = context.hist.get(s, [])
    if len(h) < MA:
        return False
    return h[-1] > sum(h[-MA:]) / MA

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    try:
        dt = datetime.datetime.strptime(str(context.trade_date), '%Y%m%d')
        cur_month = (dt.year, dt.month)
    except:
        cur_month = None

    # ===== 决策:只用截至昨收的 hist =====
    last = {s: context.hist[s][-1] for s in context.hist if context.hist.get(s)}
    rebal = cur_month is not None and cur_month != context.prev_month
    if cur_month is not None:
        context.prev_month = cur_month

    # 日常离场:跌破MA120带2%缓冲
    for s in list(context.hold):
        h = context.hist.get(s, [])
        if s not in last or len(h) < MA:
            continue
        if last[s] < (sum(h[-MA:]) / MA) * 0.98:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                SRLogger.info('离场 %s (昨收破MA%d-2%%)' % (s, MA))

    if rebal:
        cands = []
        for s in POOL:
            h = context.hist.get(s, [])
            if s not in last or len(h) < max(MA, VOLD + 1) or not _ma_ok(context, s):
                continue
            rets = np.diff(np.array(h[-(VOLD + 1):], dtype=float)) / np.array(h[-(VOLD + 1):-1], dtype=float)
            cands.append((s, float(np.std(rets))))
        cands.sort(key=lambda x: x[1])            # 波动率升序
        picks = [s for s, _ in cands[:TOPN]]

        # 已持仓仍站上MA就留着(哪怕波动率排名变了)——极低换手
        room = TOPN - len(context.hold)
        for s in picks:
            if room <= 0:
                break
            if s in context.hold or s not in last:
                continue
            qty = int(acc.total_value * 0.95 / TOPN / last[s] / 100) * 100
            if qty >= 100 and acc.cash >= qty * last[s]:
                order_shares('8888', s, qty, style=MarketOrderStyle)
                context.hold.add(s)
                room -= 1
                SRLogger.info('买入低波标的 %s %d股' % (s, qty))
            else:
                SRLogger.info('想买 %s 但现金不足' % s)

    # ===== 收盘入库 =====
    for s in list(context.hist.keys()):
        try:
            b = bar[s]
            if b is None or getattr(b, 'close', None) in (None, 0):
                continue
            context.hist[s].append(b.close)
            if len(context.hist[s]) > 250:
                context.hist[s] = context.hist[s][-250:]
        except Exception as e:
            SRLogger.info('数据异常 %s: %s' % (s, e))
            continue
