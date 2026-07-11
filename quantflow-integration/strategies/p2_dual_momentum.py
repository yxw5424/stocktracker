# -*- coding: utf-8 -*-
# P2 双动量二八 —— 绝对动量(股vs债),不是失灵的横截面动量:
# 每月比较 4 只股票宽基的60日动量,最强者若跑赢国债ETF且为正 → 全仓该宽基;
# 否则退守国债ETF。经典中国"二八轮动"结构,债券兜底本身就是大盘闸门。
# 无前视:信号用昨收,今开成交。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

EQ = ['510300.SH', '510500.SH', '159915.SZ', '588000.SH']
BOND = '511260.SH'
MOM = 60

def initialize(context):
    context.hist = {s: [] for s in EQ + [BOND]}
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

    if rebal:
        moms = {}
        for s in EQ + [BOND]:
            h = context.hist.get(s, [])
            if s in last and len(h) >= MOM and h[-MOM]:
                moms[s] = last[s] / h[-MOM] - 1
        eq_ok = [s for s in EQ if s in moms]
        target = None
        if eq_ok:
            best = max(eq_ok, key=lambda s: moms[s])
            if moms[best] > 0 and moms[best] > moms.get(BOND, 0):
                target = best
        if target is None and BOND in moms and moms[BOND] > 0:
            target = BOND   # 股弱则守债;债也走弱(动量<0)→ 全现金
        held = [s for s in (EQ + [BOND]) if acc.positions.get(s) and acc.positions[s].sellable > 0]
        for s in held:
            if s != target:
                order_shares('8888', s, -acc.positions[s].sellable, style=MarketOrderStyle)
                SRLogger.info('切换卖出 %s' % s)
        if target and target in last and target not in held:
            qty = int(acc.total_value * 0.95 / last[target] / 100) * 100
            if qty >= 100 and acc.cash >= qty * last[target]:
                order_shares('8888', target, qty, style=MarketOrderStyle)
                SRLogger.info('切换买入 %s %d股(60日动量 %.1f%%)' % (target, qty, moms[target] * 100))
            else:
                SRLogger.info('想买 %s 但现金不足(等下月)' % target)
        if target is None:
            SRLogger.info('股债动量均弱,持币观望')

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
