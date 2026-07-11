# -*- coding: utf-8 -*-
# P7 状态倾斜配置 —— "在合适的时期用合适的配置"的无预测实现:
# 不预测未来,只识别【当下可观测的趋势状态】并切换权重:
#   牛态(沪深300昨收 > MA120×1.02 后进入): 股55/债25/金20
#   熊态(跌破 MA120×0.98 后进入):          股20/债55/金25
#   滞回带±2%:在带内维持原状态,防止贴线反复横跳(P2满仓切换的死因之一)
#   预热期(MA120数据不足)用中性 40/40/20。
# 证据锚:MA120闸门在走查中把回撤-46%→-10%(只防守);本策略测它能否
# 在"防守"之外再挣到"牛市多拿股"的钱。月度执行,信号=昨收,无前视。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

EQ, BOND, GOLD = '510300.SH', '511260.SH', '518880.SH'
W_BULL = {EQ: 0.55, BOND: 0.25, GOLD: 0.20}
W_BEAR = {EQ: 0.20, BOND: 0.55, GOLD: 0.25}
W_NEUT = {EQ: 0.40, BOND: 0.40, GOLD: 0.20}
MA, BAND = 120, 0.02

def initialize(context):
    context.hist = {s: [] for s in (EQ, BOND, GOLD)}
    context.prev_month = None
    context.state = 'neutral'

def _update_state(context):
    h = context.hist.get(EQ, [])
    if len(h) < MA:
        context.state = 'neutral'
        return
    ma = sum(h[-MA:]) / MA
    px = h[-1]
    if context.state != 'bull' and px > ma * (1 + BAND):
        context.state = 'bull'
    elif context.state != 'bear' and px < ma * (1 - BAND):
        context.state = 'bear'
    elif context.state == 'neutral':
        context.state = 'bull' if px > ma else 'bear'

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

    if rebal and all(s in last for s in (EQ, BOND, GOLD)):
        _update_state(context)
        W = {'bull': W_BULL, 'bear': W_BEAR}.get(context.state, W_NEUT)
        total = acc.total_value
        orders = []
        for s, w in W.items():
            pos = acc.positions.get(s)
            cur_qty = pos.sellable if pos else 0
            tgt_qty = int(total * w * 0.95 / last[s] / 100) * 100
            delta = tgt_qty - cur_qty
            if abs(delta) * last[s] < total * 0.02 or abs(delta) < 100:
                continue
            orders.append((s, delta, w))
        for s, delta, w in sorted(orders, key=lambda x: x[1]):   # 先卖后买
            if delta < 0:
                order_shares('8888', s, delta, style=MarketOrderStyle)
                SRLogger.info('[%s态]卖出 %s %d股(目标%.0f%%)' % (context.state, s, -delta, w * 100))
            elif acc.cash >= delta * last[s]:
                order_shares('8888', s, delta, style=MarketOrderStyle)
                SRLogger.info('[%s态]买入 %s %d股(目标%.0f%%)' % (context.state, s, delta, w * 100))
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
