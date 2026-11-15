#!/usr/bin/env python3
"""
core/ivfilter.py — IV 分位计算（盘中监控用）

问题：没有历史期权 IV 数据（CBOE 只给当前快照）。
方案：用历史实现波动率（RV20）作为 IV 的参照。

  IV 分位 = 当前期权 IV 在「过去 N 天 RV20 分布」中的位置

含义：
  分位 90% → 当前期权 IV 比过去 90% 的日子都贵 → 该卖
  分位 20% → 便宜 → 别卖

回测依据：只在 IV 分位 ≥ 60% 卖，SOXS 从 −248 万变 +6.9 万
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import config  # noqa: E402

config.setup_path()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_RV_CACHE = {}          # sym → dict(ts, rv_hist)


def rv_history(sym, rng="2y", ttl=1800):
    """过去 RV20 序列（缓存 30 分钟）"""
    now = time.time()
    c = _RV_CACHE.get(sym)
    if c and now - c["ts"] < ttl:
        return c["rv"]

    from sources import yahoo
    b = yahoo.bars(sym, rng, "1d")
    if not b or len(b) < 60:
        return None
    df = pd.DataFrame(b)
    rv = (np.log(df["close"] / df["close"].shift(1))
          .rolling(20).std() * math.sqrt(252)).dropna()
    if len(rv) < 40:
        return None
    _RV_CACHE[sym] = dict(ts=now, rv=rv.values)
    return rv.values


def percentile(sym, current_iv):
    """当前 IV 在历史 RV 分布中的分位（0-100）"""
    if current_iv is None or current_iv <= 0:
        return None
    hist = rv_history(sym)
    if hist is None or len(hist) < 40:
        return None
    return float((current_iv >= hist).mean() * 100)


def iv_median_from_chain(chain, spot, band=0.05):
    """从期权链取平值附近的中位 IV"""
    ivs = [c["iv"] for c in chain
           if c.get("iv") and c["iv"] > 0
           and abs(c["strike"] / spot - 1) <= band]
    if not ivs:
        ivs = [c["iv"] for c in chain if c.get("iv") and c["iv"] > 0]
    if not ivs:
        return None
    ivs.sort()
    return ivs[len(ivs) // 2]


def grade(pct):
    """分位 → 等级"""
    if pct is None:
        return "未知", ""
    if pct >= 85:
        return "极高", "🔥"
    if pct >= 60:
        return "高", "✅"
    if pct >= 40:
        return "中", "🟡"
    return "低", "❌"


if __name__ == "__main__":
    import sys as _s
    syms = _s.argv[1:] or ["SOXS", "SQQQ", "UVXY", "VXX"]
    print("IV 分位自测（RV20 历史分布）\n")
    print(f"{'标的':>7}{'RV序列':>9}{'当前RV':>9}{'分位':>8}{'等级':>8}")
    for s in syms:
        h = rv_history(s)
        if h is None:
            print(f"{s:>7}  无数据")
            continue
        cur = float(h[-1])
        pct = float((cur >= h).mean() * 100)
        g, mark = grade(pct)
        print(f"{s:>7}{len(h):>9}{cur*100:>8.1f}%{pct:>7.0f}%"
              f"{g:>7}{mark}")
