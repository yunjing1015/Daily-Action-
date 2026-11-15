"""
sources/cboe.py — CBOE 延迟期权链封装

免费源，延迟 15 分钟。网络不稳时会重试。
注意：current_price 可能是盘中某时刻的报价，收盘后用 close 字段。

自测：
    python -m sources.cboe SPY
"""
import json
import sys
import os
import time
import urllib.request
from datetime import date, datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import config  # noqa: E402


def fetch_raw(symbol, tries=4, timeout=170):
    """原始 JSON"""
    url = f"{config.CBOE_BASE}/{symbol}.json"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=config.CBOE_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = json.loads(r.read().decode("utf-8", "replace"))
            return raw.get("data", raw)
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def underlying(d):
    """(最新价, 官方收盘, 开, 高, 低) —— 收盘后请用 close"""
    return (d.get("current_price"), d.get("close"),
            d.get("open"), d.get("high"), d.get("low"))


def parse_chain(symbol, d, max_dte=60):
    """解析成结构化 list[dict]，只保留未来 max_dte 内的合约"""
    today = date.today()
    out = []
    for o in d.get("options", []):
        s = o.get("option", "")
        try:
            body = s[len(symbol):]
            yy, mm, dd = int(body[0:2]), int(body[2:4]), int(body[4:6])
            exp = date(2000 + yy, mm, dd)
            right = body[6]
            strike = int(body[7:]) / 1000.0
        except (ValueError, IndexError):
            continue
        dte = (exp - today).days
        if dte < 0 or dte > max_dte:
            continue
        bid = float(o.get("bid") or 0)
        ask = float(o.get("ask") or 0)
        out.append(dict(
            symbol=symbol, exp=exp.isoformat(), dte=dte,
            right=right, strike=strike,
            bid=bid, ask=ask,
            mid=(bid + ask) / 2 if bid > 0 and ask > 0 else 0.0,
            oi=float(o.get("open_interest") or 0),
            volume=float(o.get("volume") or 0),
            iv=float(o.get("iv") or 0),
            delta=float(o.get("delta") or 0),
            gamma=float(o.get("gamma") or 0),
            vega=float(o.get("vega") or 0),
            theta=float(o.get("theta") or 0),
        ))
    return out


def implied_spot(chain, fallback):
    """用看涨看跌平价反推现货（比 CBOE 报价准）"""
    if not chain:
        return fallback
    exp0 = min(c["exp"] for c in chain)
    byk = {}
    for c in chain:
        if c["exp"] == exp0 and c["mid"] > 0:
            byk.setdefault(c["strike"], {})[c["right"]] = c["mid"]
    cand = []
    for k, v in byk.items():
        if "C" in v and "P" in v:
            cand.append((abs(k - fallback), k + (v["C"] - v["P"])))
    if not cand:
        return fallback
    cand.sort()
    return sum(s for _, s in cand[:5]) / min(5, len(cand))


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print(f"抓取 {sym} ...")
    d = fetch_raw(sym)
    cur, close, o, h, l = underlying(d)
    print(f"  current_price {cur}   close {close}   open {o}  high {h}  low {l}")
    chain = parse_chain(sym, d)
    print(f"  合约 {len(chain)} 个")
    if chain:
        spot = implied_spot(chain, close or cur)
        print(f"  隐含现货 {spot:.2f}")
        strikes = sorted({c['strike'] for c in chain})
        print(f"  行权价 {min(strikes):.0f} – {max(strikes):.0f}")
