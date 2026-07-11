# -*- coding: utf-8 -*-
# P4 因子动量 —— 最后一个有文献支撑的 alpha 尝试(见 A-SHARE-STRATEGY-RESEARCH.md):
# 轮动对象是「因子ETF」(红利/低波/价值/成长),不是已证死的行业/资产动量。
# 学术证据:因子动量 TSMOM 月均0.53%(t=3.41),多头端贡献79%——但 ETF 直接
# 实现是证据缺口,本策略就是来补这个证据的。
# 规则:每月首个交易日,按 20/60 日动量均值给 6 只因子ETF打分,
#   持有得分>0 的前2只(各47%);空出的仓位退守国债ETF;
#   已持仓只要仍在前3名就不换(排名滞回,降换手)。
# 无前视:信号用昨收,今开成交。录用标准:训练/测试两窗都跑赢P1且回撤≤2×P1。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

FACTOR_ETFS = ['510880.SH', '512890.SH', '510030.SH', '159905.SZ', '159967.SZ', '159966.SZ']
BOND = '511260.SH'
TOPN, KEEP = 2, 3

def initialize(context):
    context.hist = {s: [] for s in FACTOR_ETFS + [BOND]}
    context.prev_month = None

def _score(context, s):
    h = context.hist.get(s, [])
    if len(h) < 60 or not h[-20] or not h[-60]:
        return None
    return ((h[-1] / h[-20] - 1) + (h[-1] / h[-60] - 1)) / 2.0

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

    if rebal:
        scores = {s: _score(context, s) for s in FACTOR_ETFS}
        ranked = sorted([s for s in FACTOR_ETFS if scores[s] is not None],
                        key=lambda s: scores[s], reverse=True)
        picks = [s for s in ranked[:TOPN] if scores[s] > 0]
        keep = set(s for s in ranked[:KEEP] if scores[s] > 0)

        held = [s for s in FACTOR_ETFS + [BOND]
                if acc.positions.get(s) and acc.positions[s].sellable > 0]
        # 卖:因子ETF跌出前KEEP名或转负;债券仓在有新因子标的可买时腾退
        n_factor_target = len(picks) if picks else 0
        for s in held:
            if s == BOND:
                if n_factor_target > 0:
                    order_shares('8888', s, -acc.positions[s].sellable, style=MarketOrderStyle)
                    SRLogger.info('腾退债券仓,让位因子ETF')
            elif s not in keep:
                order_shares('8888', s, -acc.positions[s].sellable, style=MarketOrderStyle)
                SRLogger.info('卖出 %s (跌出前%d或动量转负)' % (s, KEEP))
        held_f = [s for s in held if s != BOND and s in keep]

        # 买:补足因子仓位至TOPN;一个都没有→全仓退守债券
        room = TOPN - len(held_f)
        for s in picks:
            if room <= 0:
                break
            if s in held_f or s not in last:
                continue
            qty = int(acc.total_value * 0.47 / last[s] / 100) * 100
            if qty >= 100 and acc.cash >= qty * last[s]:
                order_shares('8888', s, qty, style=MarketOrderStyle)
                room -= 1
                SRLogger.info('买入因子ETF %s %d股(动量分 %.1f%%)' % (s, qty, scores[s] * 100))
            else:
                SRLogger.info('想买 %s 但现金不足' % s)
        if not picks and BOND in last and BOND not in held:
            qty = int(acc.total_value * 0.94 / last[BOND] / 100) * 100
            if qty >= 100 and acc.cash >= qty * last[BOND]:
                order_shares('8888', BOND, qty, style=MarketOrderStyle)
                SRLogger.info('因子动量全弱,退守国债ETF')

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
