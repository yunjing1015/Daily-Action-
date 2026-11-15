#!/usr/bin/env python3
"""
run/intraday_backtest.py — 用盘中价格回测（修正方法论错误）

对比两种进场方式：
  A. 收盘价进场 + 收盘价差（0.15 滑点）  ← 保守，实际收盘交易
  B. 盘中 10:30 进场 + 盘中价差（0.01）  ← 真实，盘中交易

数据：Yahoo 2 年小时线

结论会告诉你：**盘中交易比收盘交易好多少**
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


def hourly_df(sym, rng="730d"):
    """Yahoo 小时线 → DataFrame（含 date, hour_et, close）"""
    b = yahoo.bars(sym, rng, "1h")
    if not b or len(b) < 500:
        return None
    df = pd.DataFrame(b)
    df["dt"] = pd.to_datetime(df["date"])
    # Yahoo 返回的 date 已按本地格式化，需要重建时间
    # 直接用原始 timestamp
    import json
    import urllib.request
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{yahoo.yahoo_code(sym)}?range={rng}&interval=1h")
    req = urllib.request.Request(url, headers=yahoo.UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    res = (j.get("chart") or {}).get("result")
    if not res:
        return None
    r0 = res[0]
    ts = r0.get("timestamp") or []
    q = (r0.get("indicators", {}).get("quote") or [{}])[0]
    closes = q.get("close") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    vols = q.get("volume") or []
    rows = []
    for i, t in enumerate(ts):
        if i >= len(closes) or closes[i] is None:
            continue
        dt = datetime.fromtimestamp(t)          # 本地时间
        rows.append(dict(ts=t, dt=dt,
                         date=dt.strftime("%Y-%m-%d"),
                         hour=dt.hour,
                         close=float(closes[i]),
                         high=float(highs[i]) if i < len(highs) and highs[i] else None,
                         low=float(lows[i]) if i < len(lows) and lows[i] else None,
                         volume=float(vols[i]) if i < len(vols) and vols[i] else 0))
    if not rows:
        return None
    out = pd.DataFrame(rows)
    # 每个交易日的第一个小时（约 9:30-10:30 或 10:30-11:30，取决于时区）
    # 取每天第一个 bar 的 close 作为「开盘一小时后」价格
    first = out.groupby("date").first().reset_index()
    first = first.rename(columns={"close": "open_1h",
                                  "high": "open_1h_high",
                                  "low": "open_1h_low"})
    daily = out.groupby("date").agg(
        close=("close", "last"), high=("high", "max"),
        low=("low", "min"), volume=("volume", "sum")).reset_index()
    merged = daily.merge(first[["date", "open_1h", "open_1h_high", "open_1h_low"]],
                         on="date", how="left")
    merged["rv20"] = (np.log(merged["close"] / merged["close"].shift(1))
                      .rolling(20).std() * math.sqrt(252))
    merged["rv20_1h"] = (np.log(merged["open_1h"] / merged["open_1h"].shift(1))
                         .rolling(20).std() * math.sqrt(252))
    return merged


def backtest(sym, use_intraday=False, sell_otm=0.15, buy_otm=0.30,
             dte=30, hold=30, n_lots=10, slip=None):
    """回测熊市看涨价差

    use_intraday=True  → 用盘中 10:30 价格 + 0.01 滑点
    use_intraday=False → 用收盘价 + 0.15 滑点
    """
    df = hourly_df(sym)
    if df is None or len(df) < dte + hold + 40:
        return None
    kind = opt.classify(sym)

    price_col = "open_1h" if use_intraday else "close"
    rv_col = "rv20_1h" if use_intraday else "rv20"
    s = slip if slip is not None else (0.01 if use_intraday else 0.15)

    prices = df[price_col].values
    rv = df[rv_col].values
    n = len(df)

    trades = []
    t = 30
    while t + hold < n:
        r = rv[t]
        S = prices[t]
        if not (r == r) or r <= 0 or not (S == S) or S <= 0:
            t += hold
            continue

        K_sell = round(S * (1 + sell_otm), 2)
        K_buy = round(S * (1 + buy_otm), 2)
        if K_buy <= K_sell:
            t += hold
            continue

        T_in = dte / 365
        cs, _ = opt.price_call(S, K_sell, T_in, r, kind)
        cb, _ = opt.price_call(S, K_buy, T_in, r, kind)
        if cs <= 0.02:
            t += hold
            continue

        credit = ((cs - s) - (cb + s)) * 100 * n_lots - 4.0 * n_lots
        width = (K_buy - K_sell) * 100 * n_lots
        ml = width - credit
        if credit <= 0 or ml <= 0:
            t += hold
            continue

        S2 = prices[t + hold]
        r2 = rv[min(t + hold, n - 1)]
        if not (r2 == r2) or r2 <= 0:
            r2 = r
        T_out = max(0, dte - hold) / 365
        vs, _ = opt.price_call(S2, K_sell, T_out, r2, kind)
        vb, _ = opt.price_call(S2, K_buy, T_out, r2, kind)
        exit_cash = ((vb + s) - (vs - s)) * 100 * n_lots
        pnl = credit + exit_cash

        trades.append(dict(pnl=pnl, ror=pnl / ml * 100,
                           move=(S2 / S - 1) * 100))
        t += hold

    if len(trades) < 5:
        return None
    tr = pd.DataFrame(trades)
    return dict(sym=sym, n=len(tr), total=tr["pnl"].sum(),
                avg_ror=tr["ror"].mean(),
                winrate=(tr["pnl"] > 0).mean() * 100,
                best=tr["ror"].max(), worst=tr["ror"].min(),
                avg_move=tr["move"].mean())


def main():
    p(f"盘中 vs 收盘 回测对比 | {datetime.now():%Y-%m-%d %H:%M}\n")
    p("A = 收盘价 + 0.15 滑点（保守）")
    p("B = 盘中10:30 + 0.01 滑点（真实）\n")

    syms = ["SOXS", "SQQQ", "UVXY", "VXX", "LABD", "TZA"]
    p("=" * 100)
    p("① 两种进场方式对比（卖15%/买30%，30天）")
    p("=" * 100)
    p(f"\n{'标的':>7}{'A交易':>7}{'A总盈亏$':>12}{'A胜率':>8}{'A最差%':>9}"
      f"{'B交易':>7}{'B总盈亏$':>12}{'B胜率':>8}{'B最差%':>9}{'B-A差':>10}")

    rows = []
    for sym in syms:
        a = backtest(sym, use_intraday=False)
        b = backtest(sym, use_intraday=True)
        if not a or not b:
            p(f"{sym:>7}  数据不足")
            continue
        p(f"{sym:>7}{a['n']:>7}{a['total']:>+12,.0f}{a['winrate']:>7.0f}%"
          f"{a['worst']:>+8.0f}%{b['n']:>7}{b['total']:>+12,.0f}"
          f"{b['winrate']:>7.0f}%{b['worst']:>+8.0f}%"
          f"{b['total']-a['total']:>+10,.0f}")
        rows.append((sym, a, b))

    p("\n" + "=" * 100)
    p("② 滑点敏感度（只用盘中价，改变滑点）")
    p("=" * 100)
    p(f"\n{'标的':>7}{'0.01(盘中)':>13}{'0.05':>12}{'0.15(收盘)':>13}{'0.30':>12}")
    for sym in syms[:4]:
        vals = []
        for s in (0.01, 0.05, 0.15, 0.30):
            r = backtest(sym, use_intraday=True, slip=s)
            vals.append(r["total"] if r else None)
        if any(v is None for v in vals):
            continue
        p(f"{sym:>7}{vals[0]:>+13,.0f}{vals[1]:>+12,.0f}"
          f"{vals[2]:>+13,.0f}{vals[3]:>+12,.0f}")

    p("\n" + "=" * 100)
    p("③ 结论")
    p("=" * 100)
    if rows:
        a_tot = sum(r[1]["total"] for r in rows)
        b_tot = sum(r[2]["total"] for r in rows)
        p(f"\n  收盘进场总盈亏: {a_tot:>+,.0f}")
        p(f"  盘中进场总盈亏: {b_tot:>+,.0f}")
        p(f"  差异: {b_tot - a_tot:>+,.0f} "
          f"({(b_tot/a_tot-1)*100 if a_tot else 0:+.0f}%)")
        better = [r[0] for r in rows if r[2]["total"] > r[1]["total"]]
        p(f"\n  盘中更好的标的: {', '.join(better) if better else '无'}")

    with open(os.path.join(config.DATA_DIR, "intraday_backtest.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()
