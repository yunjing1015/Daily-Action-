"""
sources/yahoo.py — Yahoo Finance 封装（云端可用）

GitHub Actions 里没有富途，用 Yahoo 替代：
  · 日线/分钟线 → 指标计算
  · ^VIX / ^VIX9D / ^VIX3M → VIX 期限结构
  · ES=F → 标普期货

注意：中国市场被墙，但 GitHub Actions（美国 IP）可用。
"""
import json
import time
import urllib.request
from datetime import datetime

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*",
      "Accept-Language": "en-US,en;q=0.9"}

# 常用代码映射（我们的符号 → Yahoo 代码）
YAHOO_MAP = {
    "VIX": "^VIX",
    "VIX9D": "^VIX9D",
    "VIX3M": "^VIX3M",
    "ES": "ES=F",
    "NQ": "NQ=F",
}


def yahoo_code(sym):
    return YAHOO_MAP.get(sym, sym)


def chart(sym, rng="1y", interval="1d", tries=3):
    """返回原始 JSON result[0]"""
    code = yahoo_code(sym)
    url = f"{BASE}{urllib.parse.quote(code)}?range={rng}&interval={interval}"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            res = (j.get("chart") or {}).get("result")
            if not res:
                return None
            return res[0]
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def bars(sym, rng="1y", interval="1d"):
    """日线 → list[dict]（open/high/low/close/volume）"""
    import urllib.parse  # noqa: F401  (确保 quote 可用)
    r = chart(sym, rng, interval)
    if r is None:
        return None
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get("open") or [], q.get("high") or [],
                     q.get("low") or [], q.get("close") or [],
                     q.get("volume") or [])
    out = []
    for i in range(len(ts)):
        if i >= len(c) or c[i] is None:
            continue
        out.append(dict(
            date=datetime.fromtimestamp(ts[i]).strftime("%Y-%m-%d"),
            open=o[i], high=h[i], low=l[i], close=c[i],
            volume=v[i] or 0))
    return out


def quote(sym):
    """最新报价（从日线取最后两根）"""
    b = bars(sym, "5d", "1d")
    if not b or len(b) < 2:
        return None
    return dict(px=b[-1]["close"], prev=b[-2]["close"],
                high=b[-1]["high"], low=b[-1]["low"],
                vol=b[-1]["volume"],
                chg=(b[-1]["close"] / b[-2]["close"] - 1) * 100)


def vix_term():
    """VIX 期限结构（9D / 30D / 3M）"""
    out = {}
    for name in ("VIX9D", "VIX", "VIX3M"):
        try:
            q = quote(name)
            if q:
                out[name] = q
        except Exception:
            pass
    return out


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print("Yahoo Finance 自测\n")
    for s in ("SPY", "QQQ", "SMH", "^VIX", "SVIX", "ES=F"):
        try:
            q = quote(s)
            if q:
                print(f"  {s:8s} {q['px']:>9.2f}  {q['chg']:+6.2f}%  "
                      f"量 {int(q['vol']):>12,}")
            else:
                print(f"  {s:8s} 无数据")
        except Exception as e:
            print(f"  {s:8s} ❌ {type(e).__name__}")
        time.sleep(0.3)

    print("\nVIX 期限结构：")
    t = vix_term()
    for k in ("VIX9D", "VIX", "VIX3M"):
        if k in t:
            print(f"  {k:6s} {t[k]['px']:.2f}")
