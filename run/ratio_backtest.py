#!/usr/bin/env python3
"""
run/ratio_backtest.py — PUT 比例价差 + 杠杆 ETF 衰减 回测

三部分：
  ① 杠杆 ETF 长期衰减（SOXL/SOXS/TQQQ 等）
  ② PUT 比例价差历史回测（多标的、多参数）
  ③ 组合策略：卖 call 收租 + 买 put 对冲

数据：Yahoo 5 年日线 + BS 合成期权定价
"""
import math
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config  # noqa: E402
config.setup_path()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sources import yahoo  # noqa: E402
from backtest import options as opt  # noqa: E402

LOG = []


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


CACHE = {}


def get_df(sym, rng="5y"):
    if sym in CACHE:
        return CACHE[sym]
    b = yahoo.bars(sym, rng, "1d")
    if not b or len(b) < 120:
        CACHE[sym] = None
        return None
    df = pd.DataFrame(b)
    df["rv20"] = (np.log(df["close"] / df["close"].shift(1))
                  .rolling(20).std() * math.sqrt(252))
    CACHE[sym] = df
    return df


# ============ ① 杠杆 ETF 衰减 ============
def part1_decay():
    p("=" * 96)
    p("① 杠杆 ETF 长期衰减验证")
    p("=" * 96)
    pairs = [("SOXL", "SOXX", 3), ("SOXS", "SOXX", -3),
             ("TQQQ", "QQQ", 3), ("SQQQ", "QQQ", -3),
             ("TECL", "XLK", 3), ("LABU", "XBI", 3),
             ("UPRO", "SPY", 3), ("TNA", "IWM", 3)]
    p(f"\n{'杠杆':>7}{'标的':>8}{'倍数':>6}{'5年收益':>11}{'年化':>9}"
      f"{'同期基准':>10}{'基准年化':>10}{'差距':>10}")
    rows = []
    for lev, base, mult in pairs:
        d1 = get_df(lev)
        d2 = get_df(base)
        if d1 is None or d2 is None:
            continue
        n = min(len(d1), len(d2))
        c1 = d1["close"].tail(n).values
        c2 = d2["close"].tail(n).values
        yrs = n / 252
        r1 = (c1[-1] / c1[0]) ** (1 / yrs) - 1
        r2 = (c2[-1] / c2[0]) ** (1 / yrs) - 1
        # 理论：mult × 基准年化 − 拖累
        drag = 0.5 * mult * (mult - 1) * (d2["rv20"].mean() ** 2)
        theo = mult * r2 - drag
        p(f"{lev:>7}{base:>8}{mult:>+6d}{c1[-1]/c1[0]-1:>+10.1%}"
          f"{r1:>+9.1%}{c2[-1]/c2[0]-1:>+10.1%}{r2:>+10.1%}"
          f"{(r1-theo):>+10.1%}")
        rows.append(dict(lev=lev, base=base, mult=mult, years=yrs,
                         lev_ret=r1, base_ret=r2, drag=drag, theo=theo))
    p("\n➜ 「差距」= 实际年化 − 理论年化（正=比理论好，负=被额外损耗）")
    p("➜ 理论上 3x 的拖累 ≈ 3×RV²/年（RV=基准年化波动率）")
    return rows


# ============ ② PUT 比例价差回测 ============
def backtest_ratio(sym, buy_otm=0.08, sell_otm=0.03, n_buy=10, n_sell=8,
                   dte=30, hold_days=30, iv_filter=None, cost_slip=0.01):
    """单标的 PUT 比例价差月度滚动回测

    buy_otm:  买腿的虚值程度（8% = 更远）
    sell_otm: 卖腿的虚值程度（3% = 更近）
    hold_days: 持有天数（默认到期）
    iv_filter: (rv_series, idx) → bool，是否允许开仓
    """
    df = get_df(sym)
    if df is None or len(df) < dte + hold_days + 40:
        return None
    kind = opt.classify(sym)
    close = df["close"].values
    rv = df["rv20"].values
    n = len(df)

    trades = []
    t = 30
    while t + hold_days < n:
        r = rv[t]
        if not (r == r) or r <= 0:
            t += hold_days
            continue
        if iv_filter is not None and not iv_filter(rv, t):
            t += hold_days
            continue

        S = close[t]
        K_buy = round(S * (1 - buy_otm), 2)
        K_sell = round(S * (1 - sell_otm), 2)
        if K_sell <= K_buy:
            t += hold_days
            continue

        T_in = dte / 365
        p_buy, _ = opt.price_put(S, K_buy, T_in, r, kind)
        p_sell, _ = opt.price_put(S, K_sell, T_in, r, kind)
        if p_buy <= 0.02 or p_sell <= 0.02:
            t += hold_days
            continue

        # 入场：买付 ask(+滑点)，卖收 bid(-滑点)
        cash_in = (n_sell * (p_sell - cost_slip) * 100
                   - n_buy * (p_buy + cost_slip) * 100)
        fees = (n_buy + n_sell) * 1.0 * 2
        credit = cash_in - fees

        # 出场
        S2 = close[t + hold_days]
        r2 = rv[min(t + hold_days, n - 1)]
        if not (r2 == r2) or r2 <= 0:
            r2 = r
        T_out = max(0, (dte - hold_days)) / 365
        v_buy, _ = opt.price_put(S2, K_buy, T_out, r2, kind)
        v_sell, _ = opt.price_put(S2, K_sell, T_out, r2, kind)
        exit_cash = (n_buy * (v_buy - cost_slip) * 100
                     - n_sell * (v_sell + cost_slip) * 100)
        pnl = credit + exit_cash

        margin = max(1.0, (K_sell - K_buy) * n_sell * 100)
        trades.append(dict(t=t, S=S, S2=S2, rv=r, rv2=r2,
                           ret_pct=pnl / margin * 100, pnl=pnl,
                           move=(S2 / S - 1) * 100, credit=credit))
        t += hold_days

    if len(trades) < 6:
        return None
    tr = pd.DataFrame(trades)
    return dict(sym=sym, n=len(tr),
                total=tr["pnl"].sum(),
                avg_ret=tr["ret_pct"].mean(),
                winrate=(tr["pnl"] > 0).mean() * 100,
                best=tr["ret_pct"].max(),
                worst=tr["ret_pct"].min(),
                avg_move=tr["move"].mean(),
                trades=tr)


# ============ ③ 组合策略：卖 call + 买 put ============
def backtest_combo(sym, call_otm=0.05, put_otm=0.10, dte=30, hold_days=30):
    """卖 OTM call 收租 + 买 OTM put 对冲（collar 变体）
    适合长期衰减的杠杆 ETF
    """
    df = get_df(sym)
    if df is None or len(df) < dte + hold_days + 40:
        return None
    kind = opt.classify(sym)
    close = df["close"].values
    rv = df["rv20"].values
    n = len(df)

    trades = []
    t = 30
    while t + hold_days < n:
        r = rv[t]
        if not (r == r) or r <= 0:
            t += hold_days
            continue
        S = close[t]
        K_call = round(S * (1 + call_otm), 2)
        K_put = round(S * (1 - put_otm), 2)
        T_in = dte / 365

        c_px, _ = opt.price_call(S, K_call, T_in, r, kind)
        p_px, _ = opt.price_put(S, K_put, T_in, r, kind)
        # 卖 1 call 收权利金，买 1 put 付权利金
        credit = (c_px - p_px) * 100 - 2 * 2.0

        S2 = close[t + hold_days]
        r2 = rv[min(t + hold_days, n - 1)]
        if not (r2 == r2) or r2 <= 0:
            r2 = r
        T_out = max(0, (dte - hold_days)) / 365
        c_v, _ = opt.price_call(S2, K_call, T_out, r2, kind)
        p_v, _ = opt.price_put(S2, K_put, T_out, r2, kind)
        exit_cash = (p_v - c_v) * 100

        pnl = credit + exit_cash
        trades.append(dict(t=t, S=S, S2=S2,
                           ret_pct=pnl / (S * 100) * 100, pnl=pnl,
                           move=(S2 / S - 1) * 100))
        t += hold_days

    if len(trades) < 6:
        return None
    tr = pd.DataFrame(trades)
    return dict(sym=sym, n=len(tr), total=tr["pnl"].sum(),
                avg_ret=tr["ret_pct"].mean(),
                winrate=(tr["pnl"] > 0).mean() * 100,
                worst=tr["ret_pct"].min(), best=tr["ret_pct"].max(),
                trades=tr)


def main():
    p(f"PUT 比例价差 + 杠杆衰减 回测 | {datetime.now():%Y-%m-%d %H:%M}\n")

    decay = part1_decay()

    # ---- ② 多标的 ----
    p("\n" + "=" * 96)
    p("② PUT 比例价差回测（买 8% OTM × 10，卖 3% OTM × 8，30天，持有到期）")
    p("=" * 96)
    syms = ["SPY", "QQQ", "SOXX", "SMH", "SOXL", "TQQQ", "IWM", "XLK"]
    p(f"\n{'标的':>6}{'交易数':>7}{'总盈亏$':>11}{'平均%':>8}{'胜率':>7}"
      f"{'最好%':>8}{'最差%':>8}{'平均变动%':>10}")
    results = []
    for s in syms:
        r = backtest_ratio(s)
        if not r:
            p(f"{s:>6}  数据不足")
            continue
        p(f"{s:>6}{r['n']:>7}{r['total']:>+11,.0f}{r['avg_ret']:>+7.1f}%"
          f"{r['winrate']:>6.0f}%{r['best']:>+7.1f}%{r['worst']:>+7.1f}%"
          f"{r['avg_move']:>+9.2f}%")
        results.append(r)

    # ---- 比率扫描 ----
    p("\n" + "=" * 96)
    p("③ 比率扫描（QQQ / SOXL）")
    p("=" * 96)
    p(f"\n{'标的':>6}{'买入:卖出':>10}{'交易数':>7}{'总盈亏$':>11}"
      f"{'平均%':>8}{'胜率':>7}{'最差%':>8}")
    for sym in ("QQQ", "SOXL"):
        for nb, ns in [(10, 10), (10, 9), (10, 8), (10, 7), (10, 6),
                       (12, 8), (8, 8)]:
            r = backtest_ratio(sym, n_buy=nb, n_sell=ns)
            if not r:
                continue
            p(f"{sym:>6}{nb:>6}:{ns:<3}{r['n']:>7}{r['total']:>+11,.0f}"
              f"{r['avg_ret']:>+7.1f}%{r['winrate']:>6.0f}%"
              f"{r['worst']:>+7.1f}%")

    # ---- 虚值距离扫描 ----
    p("\n" + "=" * 96)
    p("④ 虚值距离扫描（买腿位置）")
    p("=" * 96)
    p(f"\n{'标的':>6}{'买腿虚值':>9}{'卖腿虚值':>9}{'交易数':>7}"
      f"{'平均%':>8}{'胜率':>7}{'最差%':>8}")
    for sym in ("QQQ", "SOXL"):
        for b_otm, s_otm in [(0.05, 0.02), (0.08, 0.03), (0.10, 0.03),
                             (0.12, 0.04), (0.15, 0.05)]:
            r = backtest_ratio(sym, buy_otm=b_otm, sell_otm=s_otm)
            if not r:
                continue
            p(f"{sym:>6}{b_otm*100:>8.0f}%{s_otm*100:>8.0f}%"
              f"{r['n']:>7}{r['avg_ret']:>+7.1f}%"
              f"{r['winrate']:>6.0f}%{r['worst']:>+7.1f}%")

    # ---- 组合策略 ----
    p("\n" + "=" * 96)
    p("⑤ 组合策略：卖 5% OTM call + 买 10% OTM put（1:1，30天）")
    p("=" * 96)
    p(f"\n{'标的':>6}{'交易数':>7}{'总盈亏$':>11}{'平均%':>8}{'胜率':>7}"
      f"{'最好%':>8}{'最差%':>8}")
    for s in syms:
        r = backtest_combo(s)
        if not r:
            continue
        p(f"{s:>6}{r['n']:>7}{r['total']:>+11,.0f}{r['avg_ret']:>+7.1f}%"
          f"{r['winrate']:>6.0f}%{r['best']:>+7.1f}%{r['worst']:>+7.1f}%")

    with open(os.path.join(config.DATA_DIR, "ratio_backtest.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()
