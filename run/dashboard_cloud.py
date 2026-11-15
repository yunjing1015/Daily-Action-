#!/usr/bin/env python3
"""
run/dashboard_cloud.py — 云端看板（GitHub Actions 用，不依赖富途）

数据源：
  Yahoo Finance  → 日线/指标/VIX期限结构/期货
  CBOE           → 期权链 → GEX
  本地 CSV       → GEX 历史（跨运行累积）

产出：
  reports/dashboard_YYYYMMDD.html
  reports/dashboard_latest.html
  data/gex_history.csv（追加）

用法：
    python run/dashboard_cloud.py              # 全量（含 GEX）
    python run/dashboard_cloud.py --no-gex     # 跳过 CBOE（快）
"""
import csv
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config, db, universe  # noqa: E402
config.setup_path()

import pandas as pd  # noqa: E402

from indicators import calc  # noqa: E402
from sources import yahoo  # noqa: E402
import run.dashboard as dash  # noqa: E402

LOG = []


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


# ---------- 日线 → DataFrame ----------
def load_df(sym):
    b = yahoo.bars(sym, "1y", "1d")
    if not b or len(b) < 60:
        return None
    return pd.DataFrame(b)


def snaps_from_yahoo(syms):
    out = {}
    for s in syms:
        try:
            q = yahoo.quote(s)
            if q:
                out[s] = dict(px=q["px"], tag="收盘", prev=q["prev"],
                              high=q["high"], low=q["low"], vol=q["vol"],
                              amp=(q["high"] - q["low"]) / q["prev"] * 100
                              if q["prev"] else 0)
        except Exception:
            pass
    return out


# ---------- GEX（CBOE） ----------
def compute_gex(symbols=("SPY", "QQQ")):
    """拉 CBOE，算各到期 GEX，追加到 gex_history.csv"""
    from sources import cboe
    rows = []
    today = datetime.now().date()
    FIELDS = ["date", "ts", "symbol", "spot", "spot_raw", "exp", "dte",
              "total_gex", "flip", "call_wall", "put_wall",
              "maxneg_k", "maxneg_gex", "call_oi_k", "put_oi_k", "n_contract"]

    for sym in symbols:
        try:
            raw = cboe.fetch_raw(sym)
            chain = cboe.parse_chain(sym, raw, max_dte=45)
            if not chain:
                p(f"  {sym}: 无合约")
                continue
            cur, close, *_ = cboe.underlying(raw)
            S = cboe.implied_spot(chain, close or cur)
            exps = sorted({c["exp"] for c in chain})
            for e in exps:
                sub = [c for c in chain if c["exp"] == e]
                gk, oc, op = {}, {}, {}
                n = 0
                for c in sub:
                    if not c["gamma"] or not c["oi"]:
                        continue
                    n += 1
                    v = c["gamma"] * c["oi"] * 100 * S * S * 0.01
                    if c["right"] == "C":
                        gk[c["strike"]] = gk.get(c["strike"], 0) + v
                        oc[c["strike"]] = oc.get(c["strike"], 0) + c["oi"]
                    else:
                        gk[c["strike"]] = gk.get(c["strike"], 0) - v
                        op[c["strike"]] = op.get(c["strike"], 0) + c["oi"]
                if not gk:
                    continue
                tot = sum(gk.values())
                ks = sorted(gk)
                cum, flip = 0.0, None
                for k in ks:
                    cum += gk[k]
                    if flip is None and cum > 0:
                        flip = k
                band = [k for k in ks if S * 0.96 <= k <= S * 1.04]
                cw = max((k for k in band if k >= S), key=lambda k: gk[k], default=None)
                pw = max((k for k in band if k < S), key=lambda k: gk[k], default=None)
                mnk = min(ks, key=lambda k: gk[k])
                dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
                rows.append(dict(
                    date=today.isoformat(), ts=datetime.now().isoformat(timespec="seconds"),
                    symbol=sym, spot=round(S, 2), spot_raw=cur, exp=e, dte=dte,
                    total_gex=round(tot / 1e6, 1), flip=flip, call_wall=cw, put_wall=pw,
                    maxneg_k=mnk, maxneg_gex=round(gk[mnk] / 1e6, 1),
                    call_oi_k=int(oc.get(cw, 0)), put_oi_k=int(op.get(pw, 0)),
                    n_contract=n))
            p(f"  {sym}: {len(chain)} 合约，{len(exps)} 到期")
        except Exception as e:
            p(f"  {sym} 失败: {type(e).__name__}: {str(e)[:50]}")

    if not rows:
        return 0
    path = os.path.join(config.DATA_DIR, "gex_history.csv")
    exists = os.path.exists(path)
    existing = set()
    if exists:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing.add((r["date"], r["symbol"], r["exp"]))
    new = [r for r in rows if (r["date"], r["symbol"], r["exp"]) not in existing]
    if new:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerows(new)
    p(f"  GEX 新增 {len(new)} 条（总 {len(existing) + len(new)}）")
    return len(new)


def read_gex_rows(symbol="SPY", limit=8):
    path = os.path.join(config.DATA_DIR, "gex_history.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["symbol"] == symbol]
    if not rows:
        return []
    last = max(r["date"] for r in rows)
    rows = [r for r in rows if r["date"] == last]
    rows.sort(key=lambda r: int(float(r["dte"])))
    out = []
    for r in rows[:limit]:
        def f(k):
            try:
                return float(r[k]) if r[k] not in ("", None) else None
            except (ValueError, TypeError):
                return None
        out.append(dict(exp=r["exp"], dte=int(float(r["dte"])),
                        total_gex=f("total_gex"), flip=f("flip"),
                        call_wall=f("call_wall"), put_wall=f("put_wall"),
                        maxneg_k=f("maxneg_k"), maxneg_gex=f("maxneg_gex")))
    return out


# ---------- VIX 期限结构 ----------
def vix_curve_from_yahoo():
    t = yahoo.vix_term()
    out = []
    for name, label in (("VIX9D", "VIX9D (9日)"), ("VIX", "VIX (30日)"),
                        ("VIX3M", "VIX3M (3月)")):
        if name in t:
            out.append(dict(name=label, px=t[name]["px"]))
    return out


def main():
    no_gex = "--no-gex" in sys.argv
    syms = universe.all_symbols()
    p(f"云端看板 | {len(syms)} 只标的\n")

    # 1. GEX（先做，慢）
    if not no_gex:
        p("① 采集 GEX（CBOE）...")
        try:
            compute_gex(("SPY", "QQQ"))
        except Exception as e:
            p(f"  ❌ {type(e).__name__}: {str(e)[:60]}")

    # 2. 快照
    p("\n② 拉取快照（Yahoo）...")
    snaps = snaps_from_yahoo(syms)
    p(f"  ✅ {len(snaps)}/{len(syms)}")

    # 3. 指标 + 价格历史
    p("\n③ 计算指标...")
    inds = {}
    price_hist = {}
    for s in syms:
        try:
            df = load_df(s)
            if df is None:
                p(f"  {s:6s} 数据不足")
                continue
            inds[s] = calc.latest_snapshot(df)
            price_hist[s] = [float(x) for x in df["close"].tolist()]
            p(f"  {s:6s} RSI {inds[s]['rsi14'] or 0:5.1f}  "
              f"ADX {inds[s]['adx'] or 0:4.0f}  评分 {inds[s]['score']:+d}")
        except Exception as e:
            p(f"  {s} 失败: {type(e).__name__}")

    # 4. VIX 期限结构
    p("\n④ VIX 期限结构...")
    vix_curve = []
    try:
        vix_curve = vix_curve_from_yahoo()
        for c in vix_curve:
            p(f"  {c['name']:14s} {c['px']:.2f}")
    except Exception as e:
        p(f"  ❌ {type(e).__name__}")

    # 5. GEX 表格
    gex_rows = read_gex_rows("SPY")

    # 6. 生成
    html = dash.build(syms, snaps, inds, gex_rows, vix_curve, price_hist)
    stamp = datetime.now().strftime("%Y%m%d")
    out = os.path.join(config.REPORT_DIR, f"dashboard_{stamp}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    latest = os.path.join(config.REPORT_DIR, "dashboard_latest.html")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)
    p(f"\n✅ 看板：{out}")
    p(f"   {latest}")

    with open(os.path.join(config.DATA_DIR, "dashboard_cloud.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()

