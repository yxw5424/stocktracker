# -*- coding: utf-8 -*-
# A3「慢进快出」动量轮动 —— 冻结版 v1(2026-07-10 由 DeepSeek 生成,回测:
# 2022/01-2026/06 总收益319.8%、年化39.2%、夏普1.781、最大回撤-13.7%、113笔)。
# ⚠ 已知行为:周五调仓时不跳过已持仓标的,动量领先者会被反复加码,
#   单票可堆到约60%仓位(激进)。等权修正版见 v1_1。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

def initialize(context):
    context.universe = ['562500.SH', '159770.SZ', '512760.SH', '588000.SH', '159819.SZ', '588170.SH', '515070.SH', '513050.SH', '510300.SH', '518880.SH']
    context.hist = {s: [] for s in context.universe}
    context.hold = set()
    context.max_price = {}  # 记录持仓期间最高价
    context.buy_day = 4  # 周五=4

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    today = context.trade_date
    # 获取当前日期星期几
    try:
        dt = datetime.datetime.strptime(str(today), '%Y%m%d')
        weekday = dt.weekday()
    except:
        weekday = -1

    prices = {}
    for s in context.universe:
        try:
            b = bar[s]
            if b is None or getattr(b, 'close', None) in (None, 0):
                continue
            prices[s] = b.close
            context.hist[s].append(b.close)
            if len(context.hist[s]) > 250:
                context.hist[s] = context.hist[s][-250:]
        except Exception as e:
            SRLogger.info('数据异常 %s: %s' % (s, e))
            continue

    # 每日卖出检查
    for s in list(context.hold):
        if s not in prices:
            continue
        h = context.hist.get(s, [])
        if len(h) < 20:
            continue
        ma20 = sum(h[-20:]) / 20
        current_price = prices[s]
        # 跌破20日均线
        if current_price < ma20:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (跌破20日线) @ %.3f' % (s, current_price))
            continue
        # 回撤8%
        max_p = context.max_price.get(s, current_price)
        if current_price > max_p:
            context.max_price[s] = current_price
        elif max_p > 0 and (max_p - current_price) / max_p >= 0.08:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (回撤8%%) @ %.3f' % (s, current_price))

    # 每周五买入决策
    if weekday != context.buy_day:
        return

    # 计算动量与均线
    candidates = []
    for s in context.universe:
        if s not in prices:
            continue
        h = context.hist.get(s, [])
        if len(h) < 60:
            continue
        # 20日动量 = 当前价 / 20日前价 - 1
        price_20d_ago = h[-20] if len(h) >= 20 else None
        if price_20d_ago is None or price_20d_ago == 0:
            continue
        momentum = prices[s] / price_20d_ago - 1
        if momentum <= 0:
            continue
        # 60日均线
        ma60 = sum(h[-60:]) / 60
        if prices[s] <= ma60:
            continue
        candidates.append((s, momentum))

    # 选前3名
    candidates.sort(key=lambda x: x[1], reverse=True)
    top3 = [c[0] for c in candidates[:3]]

    # 卖出不在top3的持仓（释放资金）
    for s in list(context.hold):
        if s not in top3:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (调仓) @ %.3f' % (s, prices.get(s, 0)))

    # 买入top3，等权
    if not top3:
        return
    budget_per = acc.total_value * 0.95 / len(top3)  # 留5%现金
    for s in top3:
        if s not in prices:
            continue
        qty = int(budget_per / prices[s] / 100) * 100
        if qty >= 100 and acc.cash >= qty * prices[s]:
            order_shares('8888', s, qty, style=MarketOrderStyle)
            context.hold.add(s)
            context.max_price[s] = prices[s]
            SRLogger.info('买入 %s %d股 @ %.3f' % (s, qty, prices[s]))
        else:
            SRLogger.info('想买 %s 但资金不足或不足100股' % s)
