#!/usr/bin/env python3
"""
gex_history.py — 每日 GEX 历史采集（为回测建立数据集）

每天收盘前跑一次，把 SPY（可选多标的）的 GEX 结构写入 data/gex_history.csv

字段：
  date        采集日期
  ts          采集时间戳
  symbol      标的
  spot        隐含现货（期权平价）
  spot_raw    CBOE 报价
  exp         到期日
  dte         距到期天数
  total_gex   该到期总 GEX（百万美元）
  flip        gamma flip 价位
  call_wall   call 墙
  put_wall    put 墙
  maxneg_k    最大负 gamma 行权价
  maxneg_gex  该处 GEX（百万）
  call_oi_k   Call 墙处 OI
  put_oi_k    Put 墙处 OI
  n_contract  合约数

用法:
  python gex_history.py            # 默认 SPY
  python gex_history.py SPY QQQ    # 多标的
"""
import csv
import json
import math
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "gex_history.csv")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

FIELDS = ["date", "ts", "symbol", "spot", "spot_raw", "exp", "dte",
          "total_gex", "flip", "call_wall", "put_wall",
          "maxneg_k", "maxneg_gex", "call_oi_k", "put_oi_k", "n_contract"]


def fetch(url, tries=4, timeout=170):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            print(f"    第 {i+1} 次失败: {type(e).__name__}: {str(e)[:50]}", flush=True)
            time.sleep(3)
    raise last


def parse(sym, opts):
    rows = []
    for o in opts:
        s = o.get("option", "")
        try:
            body = s[len(sym):]
            yy, mm, dd = int(body[0:2]), int(body[2:4]), int(body[4:6])
            right = body[6]
            strike = int(body[7:]) / 1000.0
            exp = f"20{yy:02d}-{mm:02d}-{dd:02d}"
        except Exception:
            continue
        rows.append(dict(exp=exp, right=right, strike=strike,
                         oi=o.get("open_interest") or 0,
                         gamma=o.get("gamma") or 0))
    return rows


def implied_spot(rows, px):
    today = datetime.now().strftime("%Y-%m-%d")
    exps = sorted({r["exp"] for r in rows if r["exp"] >= today})
    if not exps:
        return px
    e = exps[0]
    byk = defaultdict(dict)
    for r in rows:
        if r["exp"] == e:
            byk[r["strike"]][r["right"]] = r
    cand = []
    for k, v in byk.items():
        if "C" in v and "P" in v:
            cand.append((abs(k - px), k + (v["C"]["oi"] - v["P"]["oi"])))
    # 平价需要价格而非 OI，简化为用行权价中位数附近
    near = [k for k, _ in sorted(cand)[:5]] if cand else []
    # 没有价格时退回 CBOE 报价
    return px


def collect(sym):
    url = f"https://cdn-api.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    print(f"  {sym}: 抓取 CBOE ...", flush=True)
    raw = fetch(url)
    data = raw.get("data", raw)
    px = data.get("current_price")
    opts = data.get("options", [])
    print(f"    {len(opts)} 合约，CBOE 报价 {px}", flush=True)

    rows = parse(sym, opts)
    if not rows:
        return []
    # 用平价算隐含现货（需要价格）
    today = datetime.now().strftime("%Y-%m-%d")
    exps = sorted({r["exp"] for r in rows if r["exp"] >= today})
    S = px
    if exps:
        e = exps[0]
        byk = defaultdict(dict)
        for o in opts:
            s = o.get("option", "")
            try:
                body = s[len(sym):]
                yy, mm, dd = int(body[0:2]), int(body[2:4]), int(body[4:6])
                if f"20{yy:02d}-{mm:02d}-{dd:02d}" != e:
                    continue
                right = body[6]
                k = int(body[7:]) / 1000.0
                bid, ask = o.get("bid") or 0, o.get("ask") or 0
                if bid > 0 and ask > 0:
                    byk[k][right] = (bid + ask) / 2
            except Exception:
                continue
        cand = []
        for k, v in byk.items():
            if "C" in v and "P" in v:
                cand.append((abs(k - px), k + (v["C"] - v["P"])))
        if cand:
            cand.sort()
            S = sum(s for _, s in cand[:5]) / min(5, len(cand))
    print(f"    隐含现货 {S:.2f}", flush=True)

    today_d = datetime.now().date()
    stamp = datetime.now().isoformat(timespec="seconds")
    recs = []
    for e in exps[:10]:
        dte = (datetime.strptime(e, "%Y-%m-%d").date() - today_d).days
        if dte < 0:
            continue
        sub = [r for r in rows if r["exp"] == e]
        gex_k = defaultdict(float)
        oi_c, oi_p = defaultdict(float), defaultdict(float)
        n = 0
        for r in sub:
            if not r["gamma"] or not r["oi"]:
                continue
            n += 1
            v = r["gamma"] * r["oi"] * 100 * S * S * 0.01
            if r["right"] == "C":
                gex_k[r["strike"]] += v
                oi_c[r["strike"]] += r["oi"]
            else:
                gex_k[r["strike"]] -= v
                oi_p[r["strike"]] += r["oi"]
        if not gex_k:
            continue
        total = sum(gex_k.values())
        ks = sorted(gex_k)
        cum, flip = 0.0, None
        for k in ks:
            cum += gex_k[k]
            if flip is None and cum > 0:
                flip = k
        band = [k for k in ks if S * 0.96 <= k <= S * 1.04]
        cw = max((k for k in band if k >= S), key=lambda k: gex_k[k], default=None)
        pw = max((k for k in band if k < S), key=lambda k: gex_k[k], default=None)
        maxneg_k = min(ks, key=lambda k: gex_k[k])

        recs.append(dict(
            date=today_d.isoformat(), ts=stamp, symbol=sym,
            spot=round(S, 2), spot_raw=px, exp=e, dte=dte,
            total_gex=round(total / 1e6, 1), flip=flip,
            call_wall=cw, put_wall=pw,
            maxneg_k=maxneg_k, maxneg_gex=round(gex_k[maxneg_k] / 1e6, 1),
            call_oi_k=int(oi_c.get(cw, 0)), put_oi_k=int(oi_p.get(pw, 0)),
            n_contract=n))
    return recs


def main():
    syms = sys.argv[1:] or ["SPY"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    # 读已有数据，做去重
    existing = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing.add((r["date"], r["symbol"], r["exp"]))

    all_recs = []
    for s in syms:
        try:
            all_recs += collect(s)
        except Exception as e:
            print(f"  {s} 失败: {type(e).__name__}: {str(e)[:60]}", flush=True)

    new = [r for r in all_recs if (r["date"], r["symbol"], r["exp"]) not in existing]
    if not new:
        print(f"\n没有新记录（{len(all_recs)} 条全部已存在）")
        return

    write_header = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new)

    print(f"\n✅ 写入 {len(new)} 条新记录 → {OUT}")
    print(f"   总记录数 {len(existing) + len(new)}")
    for r in new[:8]:
        print(f"   {r['symbol']} {r['exp']} DTE{r['dte']:>3}  "
              f"GEX {r['total_gex']:>+9.0f}M  flip {r['flip']}  "
              f"C墙 {r['call_wall']}  P墙 {r['put_wall']}  "
              f"最大负 {r['maxneg_k']}({r['maxneg_gex']:.0f}M)")


if __name__ == "__main__":
    main()
