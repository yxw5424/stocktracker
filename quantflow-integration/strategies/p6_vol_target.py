# -*- coding: utf-8 -*-
# P6 波动率目标 —— P1 的第二个候选升级:股票仓位随市场波动自动缩放。
# 依据:波动率聚集是金融数据最稳健的可预测规律(Moreira-Muir, JF 2017:
# 按波动缩放敞口改善夏普——它择的是"波动"不是"收益",与已证死的收益预测无关)。
# 规则:每月,股票权重 = 40% × clip(目标年化波动16% / 沪深300近60日实际年化波动,
# 0.4, 1.25);黄金固定20%;余下全给国债。市场狂躁→自动减股,平静→温和加回。
# 无前视:全部信号用昨收,今开成交。录用标准:两窗"年化÷回撤"都优于P1。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

EQ, BOND, GOLD = '510300.SH', '511260.SH', '518880.SH'
BASE_EQ, GOLD_W, TARGET_VOL, VOLD = 0.40, 0.20, 0.16, 60

def initialize(context):
    context.hist = {s: [] for s in (EQ, BOND, GOLD)}
    context.prev_month = None

def _ann_vol(h):
    a = np.array(h[-(VOLD + 1):], dtype=float)
    if len(a) < VOLD + 1 or (a[:-1] <= 0).any():
        return None
    return float(np.std(np.diff(a) / a[:-1]) * np.sqrt(244))

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
        vol = _ann_vol(context.hist.get(EQ, []))
        if vol and all(s in last for s in (EQ, BOND, GOLD)):
            scale = min(1.25, max(0.40, TARGET_VOL / vol))
            w = {EQ: BASE_EQ * scale, GOLD: GOLD_W}
            w[BOND] = max(0.0, 0.95 - w[EQ] - w[GOLD])
            total = acc.total_value
            orders = []
            for s, wi in w.items():
                pos = acc.positions.get(s)
                cur_qty = pos.sellable if pos else 0
                tgt_qty = int(total * wi / last[s] / 100) * 100
                delta = tgt_qty - cur_qty
                if abs(delta) * last[s] < total * 0.02 or abs(delta) < 100:
                    continue
                orders.append((s, delta, wi))
            for s, delta, wi in sorted(orders, key=lambda x: x[1]):
                if delta < 0:
                    order_shares('8888', s, delta, style=MarketOrderStyle)
                    SRLogger.info('波动目标卖出 %s %d股(权重%.0f%%,300波动%.0f%%)' % (s, -delta, wi * 100, vol * 100))
                elif acc.cash >= delta * last[s]:
                    order_shares('8888', s, delta, style=MarketOrderStyle)
                    SRLogger.info('波动目标买入 %s %d股(权重%.0f%%,300波动%.0f%%)' % (s, delta, wi * 100, vol * 100))
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
