"""
backtest/options.py — 合成期权定价引擎

问题：我们没有历史期权链数据（CBOE 只给当前快照）。
方案：用 Black-Scholes + 历史实现波动率 + put 偏斜 合成历史期权价格。

校准依据（用真实 QQQ 数据反推）：
  ATM IV ≈ 1.10 × RV20（指数）
  偏斜：10% OTM 的 put，IV 比 ATM 高约 27%
        → skew_coef ≈ 2.7
"""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

R = 0.045


def bs_put(S, K, T, sigma):
    """Black-Scholes 欧式看跌"""
    if T <= 0:
        return max(0.0, K - S)
    if sigma <= 0:
        return max(0.0, K - S) * math.exp(-R * T)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sigma ** 2) * T) / sq
    d2 = d1 - sq
    return K * math.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_call(S, K, T, sigma):
    if T <= 0:
        return max(0.0, S - K)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sigma ** 2) * T) / sq
    d2 = d1 - sq
    return S * norm.cdf(d1) - K * math.exp(-R * T) * norm.cdf(d2)


def realized_vol(close, n=20, ann=252):
    """年化实现波动率（小数）"""
    r = np.log(close / close.shift(1))
    v = r.rolling(n).std().iloc[-1] * math.sqrt(ann)
    return float(v) if v == v else 0.0


def iv_atm(rv, kind="index"):
    """ATM IV 估计

    kind: index / sector / leverage3x / leverage2x
    校准：市场长期 IV > RV（波动率风险溢价）
    """
    mult = {"index": 1.10, "sector": 1.15,
            "leverage2x": 1.18, "leverage3x": 1.22}.get(kind, 1.15)
    return max(0.05, rv * mult)


def iv_put(iv_a, S, K, skew=2.7):
    """按 moneyness 加 put 偏斜：越虚值 IV 越高"""
    if S <= 0:
        return iv_a
    otm = max(0.0, S / K - 1) if K < S else 0.0
    return iv_a * (1 + skew * otm)


def iv_call(iv_a, S, K, skew=1.2):
    """call 偏斜较弱"""
    otm = max(0.0, K / S - 1) if K > S else 0.0
    return iv_a * (1 + skew * otm)


def price_put(S, K, T, rv, kind="index", skew=2.7):
    ia = iv_atm(rv, kind)
    return bs_put(S, K, T, iv_put(ia, S, K, skew)), iv_put(ia, S, K, skew)


def price_call(S, K, T, rv, kind="index", skew=1.2):
    ia = iv_atm(rv, kind)
    return bs_call(S, K, T, iv_call(ia, S, K, skew)), iv_call(ia, S, K, skew)


def classify(sym):
    """标的类型（决定 IV 乘数）"""
    lev3 = {"SOXL", "SOXS", "TQQQ", "SQQQ", "TECL", "TECS", "LABU", "LABD",
            "YINN", "YANG", "FNGU", "FNGD", "BULZ", "WEBL", "MIDU", "DFEN",
            "NAIL", "RETL", "CURE", "UCO", "SCO", "UPRO", "SPXU", "TNA",
            "TZA", "TMF", "TMV"}
    lev2 = {"SSO", "SDS", "QLD", "QID", "ROM", "SH"}
    index = {"SPY", "QQQ", "IWM", "DIA"}
    if sym in lev3:
        return "leverage3x"
    if sym in lev2:
        return "leverage2x"
    if sym in index:
        return "index"
    return "sector"


if __name__ == "__main__":
    # 自测：验证定价是否接近真实市场
    print("合成定价校验\n")
    print(f"{'标的':>6}{'S':>9}{'K':>8}{'RV':>7}{'合成IV':>8}{'价格':>8}")
    for sym, S, K, rv in [("QQQ", 674.25, 660, 0.26),
                          ("QQQ", 674.25, 640, 0.26),
                          ("SPY", 758.0, 720, 0.18)]:
        kind = classify(sym)
        px, iv = price_put(S, K, 2.3 / 365, rv, kind)
        print(f"{sym:>6}{S:>9.2f}{K:>8.0f}{rv*100:>6.1f}%"
              f"{iv*100:>7.1f}%{px:>8.2f}")
    print("\n参考真实数据：QQQ 660P(2DTE) IV 29.5% 价 2.21")
    print("              QQQ 640P(2DTE) IV 34.0% 价 0.52")
