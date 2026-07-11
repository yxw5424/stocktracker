# -*- coding: utf-8 -*-
# B0 大盘持有 —— "跑赢大盘"的度量基准:第一天满仓买入沪深300ETF,永不动。
# 任何策略号称跑赢大盘,必须在同一张表里赢它,不靠感觉。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

EQ = '510300.SH'

def initialize(context):
    context.hist = {EQ: []}
    context.bought = False

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    h = context.hist.get(EQ, [])
    if not context.bought and h:                      # 第二天起按昨收定量,今开买入
        px = h[-1]
        qty = int(acc.total_value * 0.98 / px / 100) * 100
        if qty >= 100 and acc.cash >= qty * px:
            order_shares('8888', EQ, qty, style=MarketOrderStyle)
            context.bought = True
            SRLogger.info('满仓买入 %s %d股,此后不再交易' % (EQ, qty))
    try:
        b = bar[EQ]
        if b is not None and getattr(b, 'close', None) not in (None, 0):
            context.hist[EQ].append(b.close)
            if len(context.hist[EQ]) > 250:
                context.hist[EQ] = context.hist[EQ][-250:]
    except Exception as e:
        SRLogger.info('数据异常: %s' % e)
