#!/usr/bin/env python3
"""
gex_multi.py — 多标的 GEX 每日采集（CBOE 延迟链）

对每个标的：抓全链 → 算最近 1-2 个到期的 GEX/flip/wall/IV → 追加到
data/gex_multi.csv（带 symbol 列）。

用法:
  python gex_multi.py                        # 默认一组标的
  python gex_multi.py --symbols SPY,QQQ,SOXL
  python gex_multi.py --expiries 2
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "gex_multi.csv")
BASE = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{}.json"
R = 0.04

FIELDS = ["symbol", "date", "asof", "spot", "expiry", "dte", "atm_iv",
          "sigma_1d_pct", "gex_musd", "flip", "call_wall", "put_wall",
          "call_oi", "put_oi", "pcr_oi", "event_flags", "strikes_used"]

DEFAULT = ["SPY", "QQQ", "SOXX", "SOXL", "VXX", "UVIX"]


def fetch(sym, retries=3, timeout=110):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(BASE.format(sym),
                                         headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as e:
            last = e
            print(f"  {sym} 第 {i + 1} 次失败: {type(e).__name__}，重试")
            time.sleep(4 * (i + 1))
    print(f"  {sym} 放弃: {last}")
    return None


def implied_spot(ch, hint):
    """用 ATM 附近 5 档的 put-call parity 反推现货：S = C - P + K·e^(-rT)

    比 CBOE 的 current_price 可靠，因为它和期权报价出自同一快照。
    """
    pairs = []
    for k, legs in ch.items():
        c, p = legs.get("C"), legs.get("P")
        if not c or not p:
            continue
        cb, ca = c.get("bid") or 0, c.get("ask") or 0
        pb, pa = p.get("bid") or 0, p.get("ask") or 0
        if cb <= 0 or ca <= 0 or pb <= 0 or pa <= 0:
            continue
        if abs(k / hint - 1) > 0.03:      # 只用 ATM ±3%
            continue
        s = (cb + ca) / 2 - (pb + pa) / 2 + k
        pairs.append((abs(k - hint), s))
    if not pairs:
        return None
    pairs.sort()
    top = [s for _, s in pairs[:5]]
    return sum(top) / len(top)


def analyse(sym, d, n_exp):
    data = d.get("data") or {}
    spot = data.get("current_price")
    if not spot:
        return []
    spot = float(spot)
    lt = data.get("last_trade_time")
    asof = datetime.fromisoformat(lt).replace(tzinfo=ET) if lt else datetime.now(ET)
    today = asof.date()
    pat = re.compile(rf"^{re.escape(sym)}(\d{{2}})(\d{{2}})(\d{{2}})([CP])(\d{{8}})$")

    by_exp = defaultdict(dict)
    for o in data.get("options", []):
        m = pat.match(o.get("option", ""))
        if not m:
            continue
        yy, mm, dd, cp, k = m.groups()
        exp = date(2000 + int(yy), int(mm), int(dd))
        if exp < today or (exp == today and asof.time() >= dtime(15, 30)):
            continue
        by_exp[exp].setdefault(int(k) / 1000, {})[cp] = o

    rows = []
    # 用 put-call parity 从期权价格反推现货，保证和期权数据同一时点
    # （CBOE 的 current_price 是延迟快照，高波动标的能差 5%）
    exp_first = sorted(by_exp)[0]
    spot_fix = implied_spot(by_exp[exp_first], spot)
    if spot_fix and abs(spot_fix / spot - 1) < 0.15:
        print(f"  spot 校正: CBOE {spot:.2f} -> parity {spot_fix:.2f} "
              f"({(spot_fix/spot-1)*100:+.2f}%)")
        spot = spot_fix
    for exp in sorted(by_exp)[:n_exp]:
        ch = by_exp[exp]
        T = max((exp - today).days, 1) / 365
        F = spot
        prof, call_oi, put_oi, ivs = {}, 0.0, 0.0, []
        for k, legs in ch.items():
            for cp, sign in (("C", 1.0), ("P", -1.0)):
                o = legs.get(cp)
                if not o:
                    continue
                oi = o.get("open_interest") or 0
                if cp == "C":
                    call_oi += oi
                else:
                    put_oi += oi
                if oi > 0:
                    g = o.get("gamma") or 0
                    if g:
                        prof[k] = prof.get(k, 0.0) + sign * g * oi * 100 * F * F * 0.01
                if o.get("iv"):
                    ivs.append((k, o["iv"]))
        if not prof:
            continue
        cum, flip = 0.0, float("nan")
        for k in sorted(prof):
            prev = cum
            cum += prof[k]
            if prev < 0 <= cum:
                flip = k
                break
        ks = sorted(prof)
        cw = max((k for k in ks if spot <= k <= spot * 1.04), key=lambda k: prof[k],
                 default=float("nan"))
        pw = max((k for k in ks if spot * 0.96 <= k < spot), key=lambda k: prof[k],
                 default=float("nan"))
        ivs.sort()
        atm_iv = min(ivs, key=lambda p: abs(p[0] - spot))[1] if ivs else float("nan")
        rows.append({
            "symbol": sym,
            "date": today.isoformat(),
            "asof": asof.isoformat(timespec="seconds"),
            "spot": round(spot, 4),
            "expiry": exp.isoformat(),
            "dte": round(T * 365, 1),
            "atm_iv": round(atm_iv, 4),
            "sigma_1d_pct": round(atm_iv * math.sqrt(T) * 100, 2),
            "gex_musd": round(sum(prof.values()) / 1e6, 2),
            "flip": None if not math.isfinite(flip) else flip,
            "call_wall": None if not math.isfinite(cw) else cw,
            "put_wall": None if not math.isfinite(pw) else pw,
            "call_oi": int(call_oi),
            "put_oi": int(put_oi),
            "pcr_oi": round(put_oi / call_oi, 3) if call_oi else None,
            "event_flags": "",
            "strikes_used": len(prof),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT))
    ap.add_argument('--expiries', type=int, default=1)
    ap.add_argument('--require-window', action='store_true',
                    help='只在 ET 15:40-16:20 之间采集（GitHub Actions 用，规避夏冬令时）')
    args = ap.parse_args()
    if args.require_window:
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            print(f'{now_et:%Y-%m-%d %H:%M} ET 是周末，跳过')
            return
        if not (dtime(15, 40) <= now_et.time() <= dtime(16, 20)):
            print(f'{now_et:%H:%M} ET 不在 15:40-16:20 窗口，跳过')
            return

    syms = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]

    all_rows = []
    for s in syms:
        print(f"[{s}] 抓取...")
        d = fetch(s)
        if not d:
            continue
        rows = analyse(s, d, args.expiries)
        print(f"  {s}: {len(rows)} 条" +
              (f"  spot {rows[0]['spot']}  GEX {rows[0]['gex_musd']:+.0f}M" if rows else ""))
        all_rows.extend(rows)

    if not all_rows:
        raise SystemExit("没有任何数据")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    # 去重：同一 (标的, 日期, 到期) 只保留最新一条，避免重跑产生重复行
    keyf = lambda r: (r["symbol"], r["date"], r.get("expiry", ""))
    new_keys = {keyf(r) for r in all_rows}
    old = []
    dropped = 0
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if keyf(r) in new_keys:
                    dropped += 1
                else:
                    old.append(r)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(old + all_rows)
    print(f"\n新增 {len(all_rows)} 行，替换旧记录 {dropped} 行，"
          f"总计 {len(old) + len(all_rows)} 行 -> {OUT}")


if __name__ == "__main__":
    main()

