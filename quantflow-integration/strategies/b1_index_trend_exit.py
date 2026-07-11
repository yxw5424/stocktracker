# -*- coding: utf-8 -*-
# B1 沪深300·趋势离场版 —— 单资产"牛市策略"的完整形态:
# 满仓参与牛市 + 机械离场线(昨收跌破MA120×0.98清仓;重上MA120×1.02买回,
# ±2%滞回防横跳)。性格:牛市≈满仓持有;转熊从高点回吐后离场躲过深渊主体;
# 震荡市反复交"保费"(每次来回2-5%)。这是保险不是alpha。无前视:信号=昨收。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

EQ, MA, BAND = '510300.SH', 120, 0.02

def initialize(context):
    context.hist = {EQ: []}
    context.holding = False

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    h = context.hist.get(EQ, [])

    # ===== 决策:只用截至昨收的 hist =====
    if len(h) >= MA:
        ma = sum(h[-MA:]) / MA
        px = h[-1]
        pos = acc.positions.get(EQ)
        held = pos and pos.sellable > 0
        if held and px < ma * (1 - BAND):
            order_shares('8888', EQ, -pos.sellable, style=MarketOrderStyle)
            context.holding = False
            SRLogger.info('趋势离场:昨收%.3f跌破MA%d下带,清仓' % (px, MA))
        elif not held and px > ma * (1 + BAND):
            qty = int(acc.total_value * 0.98 / px / 100) * 100
            if qty >= 100 and acc.cash >= qty * px:
                order_shares('8888', EQ, qty, style=MarketOrderStyle)
                context.holding = True
                SRLogger.info('趋势入场:昨收%.3f站上MA%d上带,满仓' % (px, MA))
    elif h and not context.holding:
        # MA预热期:先持有(接受市场风险,与"假设当下是牛市"一致)
        px = h[-1]
        qty = int(acc.total_value * 0.98 / px / 100) * 100
        if qty >= 100 and acc.cash >= qty * px:
            order_shares('8888', EQ, qty, style=MarketOrderStyle)
            context.holding = True
            SRLogger.info('预热期先持有 %d股' % qty)

    # ===== 收盘入库 =====
    try:
        b = bar[EQ]
        if b is not None and getattr(b, 'close', None) not in (None, 0):
            context.hist[EQ].append(b.close)
            if len(context.hist[EQ]) > 250:
                context.hist[EQ] = context.hist[EQ][-250:]
    except Exception as e:
        SRLogger.info('数据异常: %s' % e)
