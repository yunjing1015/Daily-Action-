#!/usr/bin/env python3
"""
run/spread_impact2.py — 价差影响（同一批交易对比，消除选择偏差）

修正上一版的错误：
  ❌ 旧版：宽价差时跳过大部分交易，剩下的赚钱 → 假象
  ✅ 新版：锁定同一批进场日，分别算盘中/收盘的结果

对比方式：
  对每个进场机会，算：
    credit_intraday / pnl_intraday（盘中价差）
    credit_close    / pnl_close   （收盘价差）
  然后：
    ① 全部机会（不管能不能开）
    ② 只在两边都能开的机会（公平对比）
    ③ 用平均 ROR% 而不是绝对美元（消除价格水平影响）
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


def daily(sym, rng="5y"):
    b = yahoo.bars(sym, rng, "1d")
    if not b or len(b) < 120:
        return None
    df = pd.DataFrame(b)
    df["rv20"] = (np.log(df["close"] / df["close"].shift(1))
                  .rolling(20).std() * math.sqrt(252))
    return df


def collect_trades(sym, sell_otm=0.15, buy_otm=0.30, dte=30, hold=30,
                   n_lots=10, hs_intraday=0.0015, hs_close=0.095):
    """收集每个机会在两种价差下的结果（不跳过）"""
    df = daily(sym)
    if df is None or len(df) < dte + hold + 40:
        return None
    kind = opt.classify(sym)
    close = df["close"].values
    rv = df["rv20"].values
    n = len(df)

    out = []
    t = 30
    while t + hold < n:
        r = rv[t]
        S = close[t]
        if not (r == r) or r <= 0:
            t += hold
            continue
        K_sell = round(S * (1 + sell_otm), 2)
        K_buy = round(S * (1 + buy_otm), 2)
        T_in = dte / 365
        cs, _ = opt.price_call(S, K_sell, T_in, r, kind)
        cb, _ = opt.price_call(S, K_buy, T_in, r, kind)
        if cs <= 0.02:
            t += hold
            continue

        S2 = close[t + hold]
        r2 = rv[min(t + hold, n - 1)]
        if not (r2 == r2) or r2 <= 0:
            r2 = r
        T_out = max(0, dte - hold) / 365
        vs, _ = opt.price_call(S2, K_sell, T_out, r2, kind)
        vb, _ = opt.price_call(S2, K_buy, T_out, r2, kind)

        width = (K_buy - K_sell) * 100 * n_lots
        fee = 4.0 * n_lots

        def calc(hs):
            sell_px = cs * (1 - hs)
            buy_px = cb * (1 + hs)
            credit = (sell_px - buy_px) * 100 * n_lots - fee
            ml = width - credit
            if ml <= 0:
                return None, None
            exit_cash = ((vb * (1 + hs)) - (vs * (1 - hs))) * 100 * n_lots
            pnl = credit + exit_cash
            # ROR 用毛宽度（不依赖 credit，可比较）
            return credit, pnl / width * 100

        c_i, ror_i = calc(hs_intraday)
        c_c, ror_c = calc(hs_close)
        if c_i is None or c_c is None:
            t += hold
            continue

        out.append(dict(t=t, credit_i=c_i, credit_c=c_c,
                        ror_i=ror_i, ror_c=ror_c,
                        enterable_i=c_i > 0, enterable_c=c_c > 0,
                        width=width, S=S))
        t += hold

    return out or None


def main():
    p(f"价差影响（同批交易对比）| {datetime.now():%Y-%m-%d %H:%M}\n")
    p("盘中单边滑点 0.15%  vs  收盘单边滑点 9.5%\n")

    syms = ["SOXS", "SQQQ", "UVXY", "VXX", "TZA", "LABD"]
    all_stats = []

    p("=" * 104)
    p("① 每个标的：机会数 / 能开仓的比例 / 平均 ROR")
    p("=" * 104)
    p(f"\n{'标的':>7}{'总机会':>8}{'盘中可开':>10}{'收盘可开':>10}"
      f"{'盘中平均ROR':>13}{'收盘平均ROR':>13}{'差异':>10}")

    for sym in syms:
        tr = collect_trades(sym)
        if not tr:
            p(f"{sym:>7}  数据不足")
            continue
        df = pd.DataFrame(tr)
        n_all = len(df)
        n_i = int(df["enterable_i"].sum())
        n_c = int(df["enterable_c"].sum())
        ror_i = df["ror_i"].mean()
        ror_c = df["ror_c"].mean()
        p(f"{sym:>7}{n_all:>8}{n_i:>9}({n_i/n_all*100:.0f}%)"
          f"{n_c:>9}({n_c/n_all*100:.0f}%)"
          f"{ror_i:>+12.2f}%{ror_c:>+12.2f}%{ror_i-ror_c:>+9.2f}%")
        all_stats.append((sym, df, n_i, n_c))

    p("\n" + "=" * 104)
    p("② 公平对比：只在「两边都能开仓」的机会上比较")
    p("=" * 104)
    p(f"\n{'标的':>7}{'共同机会':>9}{'盘中平均ROR':>13}{'收盘平均ROR':>13}"
      f"{'差多少':>10}{'盘中胜率':>10}{'收盘胜率':>10}")
    for sym, df, _, _ in all_stats:
        both = df[df["enterable_i"] & df["enterable_c"]]
        if len(both) < 3:
            p(f"{sym:>7}{len(both):>9}  样本太少")
            continue
        ri = both["ror_i"].mean()
        rc = both["ror_c"].mean()
        wi = (both["ror_i"] > 0).mean() * 100
        wc = (both["ror_c"] > 0).mean() * 100
        p(f"{sym:>7}{len(both):>9}{ri:>+12.2f}%{rc:>+12.2f}%"
          f"{ri-rc:>+9.2f}%{wi:>9.0f}%{wc:>9.0f}%")

    p("\n" + "=" * 104)
    p("③ 结论")
    p("=" * 104)

    total_all = sum(len(df) for _, df, _, _ in all_stats)
    total_ci = sum(n_c for _, _, _, n_c in all_stats)
    total_ii = sum(n_i for _, _, n_i, _ in all_stats)
    p(f"\n  总机会: {total_all}")
    p(f"  盘中能开: {total_ii} ({total_ii/total_all*100:.0f}%)")
    p(f"  收盘能开: {total_ci} ({total_ci/total_all*100:.0f}%)")
    p(f"\n  ➜ 收盘价差让 {(1 - total_ci/total_ii)*100:.0f}% 的机会直接消失")
    p("  ➜ 这不是「赚多赚少」，是「能不能做」的问题")

    # 平均 ROR 差异
    diffs = []
    for sym, df, _, _ in all_stats:
        both = df[df["enterable_i"] & df["enterable_c"]]
        if len(both) >= 3:
            diffs.append(both["ror_i"].mean() - both["ror_c"].mean())
    if diffs:
        p(f"\n  同一批交易，盘中比收盘平均多赚 "
          f"{sum(diffs)/len(diffs):+.2f}% （按价差宽度归一化）")
        p(f"  相当于每笔多赚 {(sum(diffs)/len(diffs))/100:.3f} × 价差宽度")

    with open(os.path.join(config.DATA_DIR, "spread_impact2.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()
