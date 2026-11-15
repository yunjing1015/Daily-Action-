"""
backtest/engine.py — 向量化回测引擎

设计原则：
  · 前视偏差防护：信号 shift(1) 后才用
  · 成本必扣：手续费 + 滑点
  · 样本内外分割
  · 基准对比

用法：
    from backtest.engine import run
    res = run(prices, positions, cost=0.0005)
    print(res)
"""
import math
import sys
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np
import pandas as pd


def metrics(equity, trades=None, ann=252):
    """从净值曲线算指标"""
    eq = pd.Series(equity).dropna()
    if len(eq) < 10:
        return {}
    ret = eq.pct_change().dropna()
    n = len(eq)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = n / ann
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else 0

    vol = ret.std() * math.sqrt(ann)
    sharpe = (ret.mean() * ann) / vol if vol > 0 else 0
    downside = ret[ret < 0].std() * math.sqrt(ann)
    sortino = (ret.mean() * ann) / downside if downside > 0 else 0

    dd = (eq / eq.cummax() - 1).min()
    mdd = abs(dd)
    calmar = cagr / mdd if mdd > 0 else 0

    # 胜率
    win = (ret > 0).mean()

    return dict(total=total * 100, cagr=cagr * 100, vol=vol * 100,
                sharpe=sharpe, sortino=sortino, mdd=mdd * 100,
                calmar=calmar, winrate=win * 100, days=n)


def run(close, positions, cost=0.0005, ann=252):
    """向量化回测

    close:     pd.Series 收盘价
    positions: pd.Series 目标仓位（-1 到 1），0=空仓，1=满仓多
    cost:      每次换仓的成本率（双边）

    Returns: (equity_series, stats_dict)
    """
    close = pd.Series(close).reset_index(drop=True)
    positions = pd.Series(positions).reset_index(drop=True)

    ret = close.pct_change().fillna(0)
    # 关键：用昨天的仓位吃今天的收益（防前视）
    held = positions.shift(1).fillna(0)
    turnover = held.diff().abs().fillna(held.abs())
    strat_ret = held * ret - turnover * cost

    equity = (1 + strat_ret).cumprod()
    bench = (1 + ret).cumprod()

    stats = metrics(equity, ann=ann)
    stats["bench_total"] = (bench.iloc[-1] - 1) * 100
    stats["exposure"] = float((held != 0).mean() * 100)
    stats["turnover"] = float(turnover.sum())
    return equity, stats


def split_test(close, signal_fn, cost=0.0005, split=0.6):
    """样本内/样本外分割测试

    signal_fn: (close, ...) → positions
    split:     前 60% 做样本内，后 40% 样本外
    """
    close = pd.Series(close).reset_index(drop=True)
    pos = signal_fn(close)
    n = len(close)
    k = int(n * split)

    eq_is, st_is = run(close.iloc[:k], pos.iloc[:k], cost)
    eq_os, st_os = run(close.iloc[k:], pos.iloc[k:], cost)
    eq_all, st_all = run(close, pos, cost)

    return dict(in_sample=st_is, out_sample=st_os, full=st_all)


# ---------------- 内置策略 ----------------
def sig_ma_cross(close, fast=20, slow=50):
    """双均线：快线上穿慢线做多"""
    f = close.rolling(fast).mean()
    s = close.rolling(slow).mean()
    pos = (f > s).astype(float)
    return pos.fillna(0)


def sig_rsi_reversion(close, n=14, lo=30, hi=70):
    """RSI 均值回归：超卖买入，超买卖出"""
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pos = pd.Series(0.0, index=close.index)
    pos[rsi < lo] = 1.0
    pos[rsi > hi] = 0.0
    pos = pos.replace(0, np.nan).ffill().fillna(0)
    return pos


def sig_breakout(close, n=20):
    """唐奇安突破：破 20 日高做多，破 20 日低平仓"""
    hi = close.rolling(n).max().shift(1)
    lo = close.rolling(n).min().shift(1)
    pos = pd.Series(0.0, index=close.index)
    pos[close > hi] = 1.0
    pos[close < lo] = 0.0
    return pos.replace(0, np.nan).ffill().fillna(0)


def fmt(st):
    """格式化输出"""
    if not st:
        return "（数据不足）"
    return (f"总收益 {st['total']:>+7.2f}%  CAGR {st['cagr']:>+6.2f}%  "
            f"Sharpe {st['sharpe']:>5.2f}  MDD {st['mdd']:>5.2f}%  "
            f"Calmar {st['calmar']:>5.2f}  胜率 {st['winrate']:>5.1f}%  "
            f"仓位 {st.get('exposure', 0):>5.1f}%  基准 {st.get('bench_total', 0):>+7.2f}%")


if __name__ == "__main__":
    # 自测：用 Yahoo 拉真实数据
    sys.path.insert(0, HERE)
    from core import config
    config.setup_path()
    from sources import yahoo

    print("回测引擎自测（QQQ 真实数据）\n")
    bars = yahoo.bars("QQQ", "2y", "1d")
    if not bars:
        print("数据获取失败")
        sys.exit(1)
    df = pd.DataFrame(bars)
    close = df["close"]
    print(f"数据 {len(close)} 天  {bars[0]['date']} → {bars[-1]['date']}\n")

    strategies = [
        ("买入持有", lambda c: pd.Series(1.0, index=c.index)),
        ("MA 20/50", lambda c: sig_ma_cross(c, 20, 50)),
        ("MA 10/30", lambda c: sig_ma_cross(c, 10, 30)),
        ("RSI 均值回归", lambda c: sig_rsi_reversion(c)),
        ("20日突破", lambda c: sig_breakout(c, 20)),
    ]

    print("=" * 110)
    print("全样本回测（含 0.05% 换手成本）")
    print("=" * 110)
    for name, fn in strategies:
        pos = fn(close)
        eq, st = run(close, pos, cost=0.0005)
        print(f"{name:14s} {fmt(st)}")

    print("\n" + "=" * 110)
    print("样本内外分割（前 60% 训练，后 40% 验证）")
    print("=" * 110)
    print(f"{'策略':14s}{'样本内':>26s}{'样本外':>26s}")
    for name, fn in strategies:
        r = split_test(close, fn, cost=0.0005, split=0.6)
        is_s = r["in_sample"]
        os_s = r["out_sample"]
        print(f"{name:14s}  CAGR {is_s['cagr']:>+6.2f}% Sharpe {is_s['sharpe']:>5.2f}"
              f"      CAGR {os_s['cagr']:>+6.2f}% Sharpe {os_s['sharpe']:>5.2f}")
