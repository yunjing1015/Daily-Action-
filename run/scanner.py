#!/usr/bin/env python3
"""
run/scanner.py — 候选池扫描器（全市场粗筛）

每天收盘后扫 ~187 只流动性好的标的，找出异动机会。

筛选逻辑：
  ① 基础过滤：价格 > $5，日均成交额 > $20M
  ② 异动信号：
     · 量比 > 1.8（放量）
     · |Z20| > 1.5（偏离均值）
     · 距 52 周高点 < 3%（突破候选）
     · 距 52 周低点 < 3%（超跌候选）
     · RSI 极值（< 30 或 > 70）
     · ATR 放大（近 5 日 ATR / 60 日 ATR > 1.5）
  ③ 打分排序，输出 Top N

输出：
  data/scanner_YYYYMMDD.csv
  reports/scanner_YYYYMMDD.html
"""
import csv
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config, universe  # noqa: E402
config.setup_path()

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402

from sources import yahoo  # noqa: E402
from indicators import calc  # noqa: E402

LOG = []


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


def analyze(sym):
    """单标的分析，返回 dict 或 None"""
    try:
        bars = yahoo.bars(sym, "1y", "1d")
        if not bars or len(bars) < 60:
            return None
        df = pd.DataFrame(bars)
        c = df["close"]
        v = df["volume"]
        price = float(c.iloc[-1])
        if price < 5:
            return None
        # 日均成交额
        dollar_vol = float((c * v).tail(20).mean())
        if dollar_vol < 20e6:
            return None

        atr_s = calc.atr(df, 14)
        atr_pct = float((atr_s / c * 100).iloc[-1])
        atr_fast = float(atr_s.tail(5).mean())
        atr_slow = float(atr_s.tail(60).mean())
        atr_ratio = atr_fast / atr_slow if atr_slow else 1.0

        vol_ratio = float((v / v.rolling(20).mean()).iloc[-1])
        z20 = float(((c - c.rolling(20).mean()) / c.rolling(20).std()).iloc[-1])
        rsi14 = float(calc.rsi(c, 14).iloc[-1])
        hi52 = float(c.tail(252).max())
        lo52 = float(c.tail(252).min())
        dist_hi = (price / hi52 - 1) * 100
        dist_lo = (price / lo52 - 1) * 100
        chg = float((c.iloc[-1] / c.iloc[-2] - 1) * 100) if len(c) > 1 else 0

        # ---- 打分 ----
        score = 0
        reasons = []

        if vol_ratio > 2.5:
            score += 3
            reasons.append(f"巨量{vol_ratio:.1f}x")
        elif vol_ratio > 1.8:
            score += 2
            reasons.append(f"放量{vol_ratio:.1f}x")

        if abs(z20) > 2.5:
            score += 3
            reasons.append(f"偏离Z={z20:+.1f}")
        elif abs(z20) > 1.5:
            score += 1
            reasons.append(f"Z={z20:+.1f}")

        if dist_hi > -2:
            score += 2
            reasons.append("近52周高")
        elif dist_lo < 2:
            score += 2
            reasons.append("近52周低")

        if rsi14 < 25:
            score += 3
            reasons.append(f"超卖RSI{rsi14:.0f}")
        elif rsi14 < 32:
            score += 1
            reasons.append(f"RSI{rsi14:.0f}")
        elif rsi14 > 78:
            score += 3
            reasons.append(f"超买RSI{rsi14:.0f}")
        elif rsi14 > 68:
            score += 1
            reasons.append(f"RSI{rsi14:.0f}")

        if atr_ratio > 1.6:
            score += 2
            reasons.append(f"波动放大{atr_ratio:.1f}x")

        if abs(chg) > 4:
            score += 1
            reasons.append(f"异动{chg:+.1f}%")

        return dict(symbol=sym, price=round(price, 2), chg=round(chg, 2),
                    score=score, reasons=" · ".join(reasons),
                    rsi=round(rsi14, 1), adx=None, z20=round(z20, 2),
                    vol_ratio=round(vol_ratio, 2), atr_pct=round(atr_pct, 2),
                    dist_hi=round(dist_hi, 1), dist_lo=round(dist_lo, 1),
                    dollar_vol=round(dollar_vol / 1e6, 1))
    except Exception:
        return None


def main():
    pool = universe.SCAN_POOL
    p(f"候选池扫描 | {len(pool)} 只标的 | {datetime.now():%Y-%m-%d %H:%M}\n")

    rows = []
    fail = 0
    for i, sym in enumerate(pool, 1):
        r = analyze(sym)
        if r is None:
            fail += 1
        elif r["score"] > 0:
            rows.append(r)
        if i % 20 == 0:
            p(f"  进度 {i}/{len(pool)}  命中 {len(rows)}")

    rows.sort(key=lambda x: -x["score"])
    p(f"\n扫描完成：{len(pool)} 只 → 有效 {len(pool)-fail} 只 → 命中 {len(rows)} 只")

    # CSV
    stamp = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(config.DATA_DIR, f"scanner_{stamp}.csv")
    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        p(f"✅ CSV: {csv_path}")

    # HTML
    top = rows[:25]
    h = [f"<!DOCTYPE html><html><head><meta charset='utf-8'>",
         f"<title>候选池 {stamp}</title>",
         "<style>body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',"
         "sans-serif;margin:0;padding:20px;background:#f7f8fa}"
         "table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;"
         "box-shadow:0 1px 3px rgba(0,0,0,.06);border-radius:6px;overflow:hidden}"
         "th{background:#f0f2f5;text-align:left;padding:8px 10px;cursor:pointer}"
         "td{padding:7px 10px;border-bottom:1px solid #f0f0f0}"
         ".up{color:#d93025;font-weight:600}.dn{color:#1e8e3e;font-weight:600}"
         ".tag{display:inline-block;padding:1px 5px;border-radius:3px;font-size:11px;"
         "background:#eef2f7;margin-right:3px}"
         "h1{font-size:20px}.meta{color:#666;font-size:12px;margin-bottom:16px}"
         "</style></head><body>",
         f"<h1>候选池扫描 · 异动标的</h1>",
         f"<div class='meta'>{datetime.now():%Y-%m-%d %H:%M} · "
         f"扫描 {len(pool)} 只 · 命中 {len(rows)} 只 · 显示 Top {len(top)}</div>",
         "<table><thead><tr><th>标的</th><th>价格</th><th>涨跌%</th>"
         "<th>评分</th><th>RSI</th><th>Z20</th><th>量比</th><th>ATR%</th>"
         "<th>距高%</th><th>距低%</th><th>成交额$M</th><th>信号</th>"
         "</tr></thead><tbody>"]

    for r in top:
        cc = "up" if r["chg"] > 0 else "dn"
        tags = "".join(f"<span class='tag'>{x.strip()}</span>"
                       for x in r["reasons"].split("·") if x.strip())
        h.append(
            f"<tr><td><b>{r['symbol']}</b></td>"
            f"<td>{r['price']:.2f}</td>"
            f"<td class='{cc}'>{r['chg']:+.2f}</td>"
            f"<td><b>{r['score']}</b></td>"
            f"<td>{r['rsi']:.0f}</td>"
            f"<td>{r['z20']:+.2f}</td>"
            f"<td>{r['vol_ratio']:.2f}</td>"
            f"<td>{r['atr_pct']:.1f}</td>"
            f"<td>{r['dist_hi']:+.1f}</td>"
            f"<td>{r['dist_lo']:+.1f}</td>"
            f"<td>{r['dollar_vol']:.0f}</td>"
            f"<td>{tags}</td></tr>")
    h.append("</tbody></table>")
    h.append("<script>document.querySelectorAll('th').forEach(function(th,i){"
             "th.onclick=function(){var t=th.closest('table'),tb=t.querySelector('tbody');"
             "var rs=Array.from(tb.querySelectorAll('tr'));"
             "var asc=th.dataset.a!=='1';th.dataset.a=asc?'1':'0';"
             "rs.sort(function(a,b){var x=parseFloat(a.children[i].innerText)||0,"
             "y=parseFloat(b.children[i].innerText)||0;return asc?x-y:y-x});"
             "rs.forEach(function(r){tb.appendChild(r)})}});</script>")
    h.append("</body></html>")

    html_path = os.path.join(config.REPORT_DIR, f"scanner_{stamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    latest = os.path.join(config.REPORT_DIR, "scanner_latest.html")
    with open(latest, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    p(f"✅ HTML: {html_path}")

    p("\nTop 10：")
    for r in rows[:10]:
        p(f"  {r['symbol']:6s} {r['price']:>8.2f}  评分{r['score']:>2}  "
          f"{r['reasons'][:50]}")

    with open(os.path.join(config.DATA_DIR, "scanner_run.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()
