# -*- coding: utf-8 -*-
# 无前视动量轮动·参数化模板 —— v2_clean 崩盘(全区间-49%DD)后的防抖动改造底座。
# 诊断:信号晚一天(实盘现实)时,高换手的"追高杀跌"磨损吃掉全部利润;
# 对策全在 CFG 里,各变体只改 CFG,由 flows/a3-anti-whipsaw-flow.json 对比:
#   exit_buffer  卖出缓冲:昨收 < MA(exit_ma)×(1-buffer) 才卖(防贴线抖动)
#   trail        高点回撤止损阈值
#   keep_rank    排名滞回:已持仓跌出前 keep_rank 名才调仓卖出(买入仍只买前 top_n)
#   biweekly     True=隔周调仓(ISO周号偶数),换手减半
#   pyramid      True=调仓日对已持仓的领先者继续加码(v1旧行为)
# 决策只用截至昨收的 context.hist,当日收盘在 handle_data 末尾才入库 —— 无前视。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

CFG = {'exit_ma': 20, 'exit_buffer': 0.02, 'trail': 0.10,
       'mom_days': 20, 'ma_filter': 60, 'top_n': 3, 'keep_rank': 5,
       'biweekly': False, 'pyramid': False}

def initialize(context):
    context.universe = ['562500.SH', '159770.SZ', '512760.SH', '588000.SH', '159819.SZ', '588170.SH', '515070.SH', '513050.SH', '510300.SH', '518880.SH']
    context.hist = {s: [] for s in context.universe}
    context.hold = set()
    context.max_price = {}
    context.buy_day = 0  # 周一:用上周五收盘信号,周一开盘成交(对齐实盘)

def _rank(context, last):
    """按动量排序的候选表 [(sym, momentum), ...],只含通过过滤的。"""
    out = []
    for s in context.universe:
        h = context.hist.get(s, [])
        if s not in last or len(h) < max(CFG['ma_filter'], CFG['mom_days']):
            continue
        p0 = h[-CFG['mom_days']]
        if not p0:
            continue
        mom = last[s] / p0 - 1
        if mom <= 0:
            continue
        if last[s] <= sum(h[-CFG['ma_filter']:]) / CFG['ma_filter']:
            continue
        out.append((s, mom))
    out.sort(key=lambda x: x[1], reverse=True)
    return out

def handle_data(context, bar):
    acc = context.stock_account_dict['8888']
    try:
        dt = datetime.datetime.strptime(str(context.trade_date), '%Y%m%d')
        weekday, week_no = dt.weekday(), dt.isocalendar()[1]
    except:
        weekday, week_no = -1, 0

    # ===== 决策阶段:只用截至昨收的 hist =====
    last = {s: context.hist[s][-1] for s in context.universe if context.hist.get(s)}

    # 每日退出:MA 破位带缓冲 + 高点回撤
    for s in list(context.hold):
        if s not in last:
            continue
        h = context.hist.get(s, [])
        if len(h) < CFG['exit_ma']:
            continue
        px = last[s]
        ma = sum(h[-CFG['exit_ma']:]) / CFG['exit_ma']
        sold = False
        if px < ma * (1 - CFG['exit_buffer']):
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (昨收破MA%d带%.0f%%缓冲)' % (s, CFG['exit_ma'], CFG['exit_buffer'] * 100))
                sold = True
        if sold:
            continue
        mx = context.max_price.get(s, px)
        if px > mx:
            context.max_price[s] = px
        elif mx > 0 and (mx - px) / mx >= CFG['trail']:
            pos = acc.positions.get(s)
            if pos and pos.sellable > 0:
                order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                context.hold.discard(s)
                context.max_price.pop(s, None)
                SRLogger.info('卖出 %s (较高点回撤%.0f%%)' % (s, CFG['trail'] * 100))

    # 调仓日:周一(可选隔周)
    rebal = (weekday == context.buy_day) and (not CFG['biweekly'] or week_no % 2 == 0)
    if rebal:
        ranked = _rank(context, last)
        order = [s for s, _ in ranked]
        top_buy = order[:CFG['top_n']]
        keep = set(order[:max(CFG['keep_rank'], CFG['top_n'])])

        # 滞回卖出:已持仓跌出前 keep_rank 名才卖(不是跌出 top_n 就卖)
        for s in list(context.hold):
            if s not in keep:
                pos = acc.positions.get(s)
                if pos and pos.sellable > 0:
                    order_shares('8888', s, -pos.sellable, style=MarketOrderStyle)
                    context.hold.discard(s)
                    context.max_price.pop(s, None)
                    SRLogger.info('卖出 %s (跌出前%d名)' % (s, CFG['keep_rank']))

        # 买入:只补空出的仓位;pyramid=False 时跳过已持仓
        targets = [s for s in top_buy if CFG['pyramid'] or s not in context.hold]
        room = max(CFG['top_n'] - (0 if CFG['pyramid'] else len(context.hold)), 0)
        targets = targets[:room] if not CFG['pyramid'] else targets
        if targets:
            budget_per = acc.total_value * 0.95 / CFG['top_n']
            for s in targets:
                if s not in last:
                    continue
                qty = int(budget_per / last[s] / 100) * 100
                if qty >= 100 and acc.cash >= qty * last[s]:
                    order_shares('8888', s, qty, style=MarketOrderStyle)
                    context.hold.add(s)
                    context.max_price[s] = last[s]
                    SRLogger.info('买入 %s %d股 (按昨收%.3f定量)' % (s, qty, last[s]))
                else:
                    SRLogger.info('想买 %s 但资金不足或不足100股' % s)

    # ===== 收盘入库:今天的收盘价此刻才可见于下个交易日 =====
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
