# -*- coding: utf-8 -*-
# P1 静态配置·月再平衡 —— 「诚实基准」:不预测任何东西,股/债/金 40/40/20,
# 每月首个交易日回到目标权重(偏离超总资产2%才动,防无谓换手)。
# 它的意义:任何"策略"打不过它,就没有资格上实盘。无前视:定量用昨收,今开成交。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

W = {'510300.SH': 0.40, '511260.SH': 0.40, '518880.SH': 0.20}

def initialize(context):
    context.hist = {s: [] for s in W}
    context.prev_month = None

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

    if rebal and all(s in last for s in W):
        total = acc.total_value
        # 先卖后买,腾出资金
        orders = []
        for s, w in W.items():
            pos = acc.positions.get(s)
            cur_qty = pos.sellable if pos else 0
            tgt_qty = int(total * w / last[s] / 100) * 100
            delta = tgt_qty - cur_qty
            if abs(delta) * last[s] < total * 0.02 or abs(delta) < 100:
                continue   # 偏离<2%不动
            orders.append((s, delta))
        for s, delta in sorted(orders, key=lambda x: x[1]):   # 负的(卖)在前
            if delta < 0:
                order_shares('8888', s, delta, style=MarketOrderStyle)
                SRLogger.info('再平衡卖出 %s %d股' % (s, -delta))
            elif acc.cash >= delta * last[s]:
                order_shares('8888', s, delta, style=MarketOrderStyle)
                SRLogger.info('再平衡买入 %s %d股' % (s, delta))
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
