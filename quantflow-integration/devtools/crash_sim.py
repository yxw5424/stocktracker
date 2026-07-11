# -*- coding: utf-8 -*-
"""
牛市终结压力测试 —— 合成五种"牛市死法"情景,对比三种应对方式的12个月旅程:
  P1  股债金40/40/20,月度再平衡(带2%偏离阈值)——注意:再平衡=下跌途中买股票!
  B0  满仓沪深300死拿
  B1  满仓+趋势离场(昨收破MA120×0.98清仓,回MA120×1.02买回)
情景(参数标注在各函数,量级取自A股历史):
  S1 急崩2015式   先冲顶+8%,两个月-35%,再阴跌,后企稳
  S2 阴跌2021式   十二个月磨掉-30%
  S3 V型2020式    两周-15%,三个月收复,继续涨
  S4 牛市延续     全年+18%(对照:虚惊一场)
  S5 跌了不回头   六个月-40%,然后永远横盘(日式,检验"越跌越买"的最坏情况)
债券:年化+2%,股灾月避险加成;黄金:年化+6%,股灾月避险加成。
前置250天旧牛市历史供MA120计算。全部信号用昨收、次日执行(无前视)。
用法:python devtools/crash_sim.py
"""
import math
import random

DAYS, PRE = 250, 250          # 情景12个月 + 前置历史
REB_EVERY = 21                # 月度再平衡
BAND = 0.02
W = {"eq": 0.40, "bond": 0.40, "gold": 0.20}
MA, HYST = 120, 0.02


def _noisy(path, vol, seed):
    rnd = random.Random(seed)
    return [p * (1 + rnd.gauss(0, vol)) for p in path]


def _seg(start, end, n):
    """几何插值 n 天"""
    r = (end / start) ** (1 / n)
    out, p = [], start
    for _ in range(n):
        p *= r
        out.append(p)
    return out


def scenario_eq(name):
    """返回情景期的股票价格路径(起点=100,前一天是旧牛市顶)"""
    if name == "S1急崩2015式":
        path = _seg(100, 108, 40) + _seg(108, 70, 42) + _seg(70, 63, 42) + _seg(63, 65, 126)
    elif name == "S2阴跌2021式":
        path = _seg(100, 70, 250)
    elif name == "S3V型2020式":
        path = _seg(100, 102, 20) + _seg(102, 87, 10) + _seg(87, 103, 63) + _seg(103, 108, 157)
    elif name == "S4牛市延续":
        path = _seg(100, 118, 250)
    elif name == "S5跌了不回头":
        path = _seg(100, 60, 126) + [60] * 124
    return _noisy(path, 0.008, hash(name) % 1000)[:DAYS]


def scenario_defensive(eq_path):
    """债/金路径:基础漂移+股灾月避险加成(股票当月跌>3%时)"""
    bond, gold = [100.0], [100.0]
    for i in range(1, DAYS):
        month_chg = eq_path[i] / eq_path[max(0, i - 21)] - 1
        crash_boost_b = 0.0004 if month_chg < -0.03 else 0.0
        crash_boost_g = 0.0006 if month_chg < -0.03 else 0.0
        bond.append(bond[-1] * (1 + 0.02 / 250 + crash_boost_b))
        gold.append(gold[-1] * (1 + 0.06 / 250 + crash_boost_g + random.Random(i).gauss(0, 0.004)))
    return bond, gold


def run_p1(eq, bond, gold):
    """P1:月度再平衡到40/40/20;记录再平衡动作。信号=昨收,当日执行(近似)。"""
    cash = 0.0
    qty = {"eq": W["eq"] * 100 / eq[0], "bond": W["bond"] * 100 / bond[0],
           "gold": W["gold"] * 100 / gold[0]}   # 初始100万元→归一为100
    hist_v, buys = [], []
    px = {"eq": eq, "bond": bond, "gold": gold}
    for i in range(DAYS):
        total = cash + sum(qty[a] * px[a][i] for a in qty)
        hist_v.append(total)
        if i % REB_EVERY == 0 and i > 0:
            for a in qty:
                tgt_val = total * W[a]
                cur_val = qty[a] * px[a][i]
                delta = tgt_val - cur_val
                if abs(delta) > total * BAND:
                    qty[a] += delta / px[a][i]
                    cash -= delta
                    if a == "eq":
                        buys.append((i, delta))
    return hist_v, buys


def run_b0(eq):
    return [100 * p / eq[0] for p in eq], []


def run_b1(eq, pre_eq):
    """B1:趋势离场。用前置历史+情景拼接算MA120,信号=昨收。"""
    full = pre_eq + eq
    cash, qty, hist_v, trades = 0.0, 100 / eq[0], [], []
    holding = True
    for i in range(DAYS):
        j = PRE + i                              # full 中的位置
        ma = sum(full[j - MA:j]) / MA            # 截至昨收的MA
        px_y = full[j - 1]                       # 昨收
        px = full[j]
        if holding and px_y < ma * (1 - HYST):
            cash = qty * px
            qty, holding = 0.0, False
            trades.append((i, "卖"))
        elif not holding and px_y > ma * (1 + HYST):
            qty = cash / px
            cash, holding = 0.0, True
            trades.append((i, "买"))
        hist_v.append(cash + qty * px)
    return hist_v, trades


def metrics(v):
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    return v[-1] / v[0] - 1, mdd


def main():
    pre_eq = _seg(83, 100, PRE)                  # 前置一年旧牛市:+20%
    print(f"{'情景':<12}{'策略':<14}{'12个月收益':>10}{'最深回撤':>10}  动作")
    print("-" * 66)
    for name in ("S1急崩2015式", "S2阴跌2021式", "S3V型2020式", "S4牛市延续", "S5跌了不回头"):
        eq = scenario_eq(name)
        bond, gold = scenario_defensive(eq)
        for label, (v, acts) in (
                ("P1配置", run_p1(eq, bond, gold)),
                ("B0满仓死拿", run_b0(eq)),
                ("B1趋势离场", run_b1(eq, pre_eq))):
            r, mdd = metrics(v)
            if label == "P1配置":
                note = f"再平衡买股{sum(1 for _, d in acts if d > 0)}次" if acts else "未触发"
            elif label == "B1趋势离场":
                note = " ".join(f"第{i}天{s}" for i, s in acts) or "全程持有"
            else:
                note = "-"
            print(f"{name:<12}{label:<14}{r:>9.1%}{mdd:>10.1%}  {note}")
        print()


if __name__ == "__main__":
    main()
