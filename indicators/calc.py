"""
indicators/calc.py — 六大类指标计算

① 趋势  ② 动量  ③ 波动  ④ 量能  ⑤ 统计  ⑥ 结构

输入：pandas.DataFrame（含 open/high/low/close/volume）
输出：dict（最新值 + 历史序列，便于画图）
"""
import math
import numpy as np
import pandas as pd


# ---------- ① 趋势 ----------
def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def macd(close, fast=12, slow=26, signal=9):
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    return dif, dea, (dif - dea) * 2


def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    pdi = 100 * pd.Series(plus, index=df.index).rolling(n).mean() / atr
    mdi = 100 * pd.Series(minus, index=df.index).rolling(n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.rolling(n).mean(), pdi, mdi


def donchian(df, n=20):
    return df["high"].rolling(n).max(), df["low"].rolling(n).min()


# ---------- ② 动量 ----------
def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def roc(close, n=10):
    return (close / close.shift(n) - 1) * 100


def momentum_factor(close, n=252, skip=21):
    """学术派动量：过去 n 天（跳过最近 skip 天）的收益"""
    if len(close) < n + 1:
        n = max(60, len(close) - skip - 1)
    return (close.shift(skip) / close.shift(n + skip) - 1) * 100


# ---------- ③ 波动 ----------
def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n).mean()


def bollinger(close, n=20, k=2):
    m = close.rolling(n).mean()
    sd = close.rolling(n).std()
    return m - k * sd, m, m + k * sd, (2 * k * sd) / m * 100  # 带宽 %


def realized_vol(close, n=20, ann=252):
    r = np.log(close / close.shift(1))
    return r.rolling(n).std() * math.sqrt(ann) * 100


def atr_pct(df, n=14):
    """ATR 占价格百分比 —— 仓位计算用"""
    return atr(df, n) / df["close"] * 100


# ---------- ④ 量能 ----------
def obv(close, volume):
    sign = np.sign(close.diff()).fillna(0)
    return (sign * volume).cumsum()


def volume_ratio(volume, n=20):
    return volume / volume.rolling(n).mean()


def vwap(df, n=20):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum()


# ---------- ⑤ 统计 ----------
def zscore(s, n=20):
    return (s - s.rolling(n).mean()) / s.rolling(n).std()


def percentile_rank(s, n=252):
    return s.rolling(n).apply(lambda x: (x.iloc[-1] >= x).mean() * 100, raw=False)


def half_life(s, n=60):
    """均值回归半衰期（天）"""
    x = s.tail(n)
    if len(x) < 10:
        return None
    y = x.diff().dropna()
    xx = x.shift(1).dropna()
    try:
        beta = np.polyfit(xx, y, 1)[0]
        if beta >= 0:
            return None
        return -math.log(2) / beta
    except Exception:
        return None


# ---------- 汇总 ----------
def compute_all(df):
    """一次算完所有指标，返回 dict（每个值为 pandas.Series 或标量）"""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    out = {}

    # 趋势
    for n in (5, 10, 20, 50, 200):
        out[f"MA{n}"] = sma(c, n)
    dif, dea, hist = macd(c)
    out["MACD_DIF"] = dif
    out["MACD_DEA"] = dea
    out["MACD_HIST"] = hist
    a, pdi, mdi = adx(df)
    out["ADX"] = a
    out["PDI"] = pdi
    out["MDI"] = mdi

    # 动量
    out["RSI14"] = rsi(c, 14)
    out["ROC10"] = roc(c, 10)
    out["MOM"] = momentum_factor(c)

    # 波动
    out["ATR14"] = atr(df, 14)
    out["ATR_PCT"] = atr_pct(df, 14)
    bb_lo, bb_mid, bb_up, bb_w = bollinger(c)
    out["BB_LO"], out["BB_MID"], out["BB_UP"] = bb_lo, bb_mid, bb_up
    out["BB_WIDTH"] = bb_w
    out["RV20"] = realized_vol(c, 20)

    # 量能
    out["OBV"] = obv(c, v)
    out["VOL_RATIO"] = volume_ratio(v)
    out["VWAP20"] = vwap(df)

    # 统计
    out["Z20"] = zscore(c, 20)
    out["PCT252"] = percentile_rank(c, 252)

    return out


def latest_snapshot(df):
    """取最新的关键指标值（用于表格）"""
    ind = compute_all(df)
    c = df["close"].iloc[-1]
    prev = df["close"].iloc[-2] if len(df) > 1 else c

    def last(k, default=None):
        v = ind.get(k)
        if v is None:
            return default
        try:
            x = v.iloc[-1]
            return float(x) if x == x else default
        except Exception:
            try:
                return float(v)
            except Exception:
                return default

    ma20 = last("MA20")
    ma50 = last("MA50")
    rsi14 = last("RSI14")
    adx_v = last("ADX")
    atr_p = last("ATR_PCT")
    z = last("Z20")
    pct = last("PCT252")
    vw = last("VWAP20")
    vol_r = last("VOL_RATIO")

    # 综合信号打分（简单规则，可调）
    score = 0
    notes = []
    if ma20 and c > ma20:
        score += 1
    else:
        notes.append("破MA20")
    if ma50 and c > ma50:
        score += 1
    else:
        notes.append("破MA50")
    if rsi14 is not None:
        if rsi14 > 70:
            score -= 1
            notes.append("RSI超买")
        elif rsi14 < 30:
            score += 1
            notes.append("RSI超卖")
    if adx_v and adx_v > 25:
        notes.append(f"强趋势({adx_v:.0f})")
    if z is not None and abs(z) > 2:
        notes.append(f"Z={z:+.1f}")
    if vol_r and vol_r > 2:
        notes.append(f"放量{vol_r:.1f}x")

    return dict(
        close=c, chg=(c / prev - 1) * 100 if prev else 0,
        ma20=ma20, ma50=ma50, rsi14=rsi14, adx=adx_v,
        atr_pct=atr_p, z20=z, pct252=pct, vwap20=vw, vol_ratio=vol_r,
        bb_width=last("BB_WIDTH"), rv20=last("RV20"),
        score=score, notes=notes)
