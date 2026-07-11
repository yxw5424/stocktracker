# -*- coding: utf-8 -*-
# P5 风险平价 —— P1 的第一个候选升级:权重不再固定 40/40/20,
# 每月按 60 日波动率倒数分配(高波资产少拿,各资产风险贡献趋于均衡),
# 单资产权重夹在 [10%, 60%] 防极端。不预测收益,只塑造风险。
# 无前视:全部信号用昨收,今开成交。录用标准:两窗"年化÷回撤"都优于P1。
from panda_backtest.api.api import *
from panda_backtest.api.stock_api import *
import numpy as np
import pandas as pd
import datetime

ASSETS = ['510300.SH', '511260.SH', '518880.SH']
VOLD, W_MIN, W_MAX = 60, 0.10, 0.60

def initialize(context):
    context.hist = {s: [] for s in ASSETS}
    context.prev_month = None

def _vol(h):
    a = np.array(h[-(VOLD + 1):], dtype=float)
    if len(a) < VOLD + 1 or (a[:-1] <= 0).any():
        return None
    return float(np.std(np.diff(a) / a[:-1]))

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
        vols = {s: _vol(context.hist.get(s, [])) for s in ASSETS}
        if all(v for v in vols.values()) and all(s in last for s in ASSETS):
            inv = {s: 1.0 / vols[s] for s in ASSETS}
            tot_inv = sum(inv.values())
            w = {s: min(W_MAX, max(W_MIN, inv[s] / tot_inv)) for s in ASSETS}
            norm = sum(w.values())
            w = {s: w[s] / norm * 0.95 for s in ASSETS}   # 留5%现金
            total = acc.total_value
            orders = []
            for s in ASSETS:
                pos = acc.positions.get(s)
                cur_qty = pos.sellable if pos else 0
                tgt_qty = int(total * w[s] / last[s] / 100) * 100
                delta = tgt_qty - cur_qty
                if abs(delta) * last[s] < total * 0.02 or abs(delta) < 100:
                    continue
                orders.append((s, delta, w[s]))
            for s, delta, wi in sorted(orders, key=lambda x: x[1]):   # 先卖后买
                if delta < 0:
                    order_shares('8888', s, delta, style=MarketOrderStyle)
                    SRLogger.info('风险平价卖出 %s %d股(目标权重%.0f%%)' % (s, -delta, wi * 100))
                elif acc.cash >= delta * last[s]:
                    order_shares('8888', s, delta, style=MarketOrderStyle)
                    SRLogger.info('风险平价买入 %s %d股(目标权重%.0f%%)' % (s, delta, wi * 100))
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
