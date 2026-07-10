# -*- coding: utf-8 -*-
# A3「慢进快出」动量轮动 —— v2 无前视版(clean)。
#
# 为什么要有这个版本:审计发现平台工作流回测 matching_type=1(当日【开盘价】撮合),
# 而 v1 在 handle_data 里读 bar.close(当日收盘,盘中根本不可知)算信号 → 决策用了
# 未来的收盘价、成交却按当天开盘 = 前视偏差,回测数字系统性偏乐观。
#
# v2 的修法:所有信号只用【昨日及更早的收盘价】,当日开盘成交 ——
# 这正是实盘复核官的真实节奏:T日收盘后算信号 → T+1 开盘成交。
#   · 每日卖出检查:用截至昨收的序列判断,今晨开盘卖出。
#   · 调仓改在【周一】:用上周五收盘的动量排序,周一开盘买入(实盘周五收盘跑
#     reviewer、周一开盘成交,完全对齐)。
#   · 当日收盘价在 handle_data 末尾才入库,决策环节碰不到。
# 其余规则(MA20 止损、8% 回撤、20日动量+MA60 过滤、top3、不跳过已持仓的加码
# 行为)与冻结版 v1 保持一致,便于对比"前视偏差到底虚增了多少"。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

def initialize(context):
    context.universe = ['562500.SH', '159770.SZ', '512760.SH', '588000.SH', '159819.SZ', '588170.SH', '515070.SH', '513050.SH', '510300.SH', '518880.SH']
    context.hist = {s: [] for s in context.universe}   # 只存「昨日及以前」的收盘
    context.hold = set()
    context.max_price = {}  # 持仓期间最高(昨)收盘
    context.buy_day = 0  # 周一=0:用上周五收盘信号,周一开盘成交(对齐实盘)

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    today = context.trade_date
    try:
        dt = datetime.datetime.strptime(str(today), '%Y%m%d')
        weekday = dt.weekday()
    except:
        weekday = -1

    # ===== 决策阶段:只允许用 context.hist(截至昨收),不碰今天的 bar =====
    last = {}   # 各标的最近一个「已知」收盘价(=昨收)
    for s in context.universe:
        h = context.hist.get(s, [])
        if h:
            last[s] = h[-1]

    # 每日卖出检查(信号=昨收,成交=今开)
    for s in list(context.hold):
        if s not in last:
            continue
        h = context.hist.get(s, [])
        if len(h) < 20:
            continue
        ma20 = sum(h[-20:]) / 20
        px = last[s]
        sold = False
        if px < ma20:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (昨收破20日线) 今开成交' % s)
                sold = True
        if sold:
            continue
        max_p = context.max_price.get(s, px)
        if px > max_p:
            context.max_price[s] = px
        elif max_p > 0 and (max_p - px) / max_p >= 0.08:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (较高点回撤8%%) 今开成交' % s)

    # 周一调仓(动量排序基于上周五收盘)
    if weekday == context.buy_day:
        candidates = []
        for s in context.universe:
            if s not in last:
                continue
            h = context.hist.get(s, [])
            if len(h) < 60:
                continue
            p20 = h[-20]
            if not p20:
                continue
            momentum = last[s] / p20 - 1
            if momentum <= 0:
                continue
            ma60 = sum(h[-60:]) / 60
            if last[s] <= ma60:
                continue
            candidates.append((s, momentum))
        candidates.sort(key=lambda x: x[1], reverse=True)
        top3 = [c[0] for c in candidates[:3]]

        for s in list(context.hold):
            if s not in top3:
                pos = acc.positions.get(s)
                if pos and pos.sellable > 0:
                    order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                    context.hold.discard(s)
                    context.max_price.pop(s, None)
                    SRLogger.info('卖出 %s (调仓) 今开成交' % s)

        if top3:
            budget_per = acc.total_value * 0.95 / len(top3)
            for s in top3:
                if s not in last:
                    continue
                qty = int(budget_per / last[s] / 100) * 100
                if qty >= 100 and acc.cash >= qty * last[s]:
                    order_shares('8888', s, qty, style=MarketOrderStyle)
                    context.hold.add(s)
                    context.max_price[s] = last[s]
                    SRLogger.info('买入 %s %d股 (按昨收%.3f定量,今开成交)' % (s, qty, last[s]))
                else:
                    SRLogger.info('想买 %s 但资金不足或不足100股' % s)

    # ===== 收盘入库阶段:今天的收盘价此刻才写进历史,下个交易日才可用 =====
    for s in context.universe:
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
