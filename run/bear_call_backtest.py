#!/usr/bin/env python3
"""
run/bear_call_backtest.py — 熊市看涨价差（卖 call + 买保护 call）回测

策略：卖 X% OTM call，买 Y% OTM call（Y > X）
      → 定义风险，防范脉冲式暴涨爆仓

标的：3x 反向 ETF + 做多波动率 ETF（全部长期归零）
  SOXS, SQQQ, LABD, TZA      ← 3x 反向
  UVIX, UVXY, VXX, VIXY      ← 做多波动率

输出：
  data/bear_call_YYYYMMDD.csv
  reports/bear_call_YYYYMMDD.html（含图表）
"""
import csv
import math
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config, charts  # noqa: E402
config.setup_path()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sources import yahoo  # noqa: E402
from backtest import options as opt  # noqa: E402

LOG = []
CACHE = {}

# 长期归零的标的（结构性衰减）
DECAY_TARGETS = {
    "SOXS": "3x 半导体反向",
    "SQQQ": "3x 纳指反向",
    "LABD": "3x 生物反向",
    "TZA":  "3x 小盘反向",
    "UVIX": "2x 做多波动率",
    "UVXY": "1.5x 做多波动率",
    "VXX":  "1x 做多波动率",
    "VIXY": "1x 做多波动率",
    "SOXL": "3x 半导体正向（对照）",
    "TQQQ": "3x 纳指正向（对照）",
}


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


def get_df(sym, rng="5y"):
    if sym in CACHE:
        return CACHE[sym]
    b = yahoo.bars(sym, rng, "1d")
    if not b or len(b) < 120:
        CACHE[sym] = None
        return None
    df = pd.DataFrame(b)
    df["rv20"] = (np.log(df["close"] / df["close"].shift(1))
                  .rolling(20).std() * math.sqrt(252))
    CACHE[sym] = df
    return df


# ---------------- 熊市看涨价差 ----------------
def backtest_bear_call(sym, sell_otm=0.15, buy_otm=0.35,
                       dte=30, hold=30, width_lots=1,
                       iv_rank_filter=None, slip=0.01):
    """卖 sell_otm 的 call，买 buy_otm 的 call

    Returns: dict 汇总 + trades DataFrame
    """
    df = get_df(sym)
    if df is None or len(df) < dte + hold + 40:
        return None
    kind = opt.classify(sym)
    close = df["close"].values
    rv = df["rv20"].values
    n = len(df)

    trades = []
    t = 30
    while t + hold < n:
        r = rv[t]
        if not (r == r) or r <= 0:
            t += hold
            continue
        if iv_rank_filter is not None and not iv_rank_filter(rv, t):
            t += hold
            continue

        S = close[t]
        K_sell = round(S * (1 + sell_otm), 2)
        K_buy = round(S * (1 + buy_otm), 2)
        T_in = dte / 365

        c_sell, _ = opt.price_call(S, K_sell, T_in, r, kind)
        c_buy, _ = opt.price_call(S, K_buy, T_in, r, kind)
        if c_sell <= 0.02:
            t += hold
            continue

        # 卖收 bid(-滑点)，买付 ask(+滑点)
        credit = ((c_sell - slip) - (c_buy + slip)) * 100 * width_lots - 4.0
        width = (K_buy - K_sell) * 100 * width_lots
        max_loss = width - credit
        if credit <= 0 or max_loss <= 0:
            t += hold
            continue

        S2 = close[t + hold]
        r2 = rv[min(t + hold, n - 1)]
        if not (r2 == r2) or r2 <= 0:
            r2 = r
        T_out = max(0, dte - hold) / 365
        v_sell, _ = opt.price_call(S2, K_sell, T_out, r2, kind)
        v_buy, _ = opt.price_call(S2, K_buy, T_out, r2, kind)
        exit_cash = ((v_buy + slip) - (v_sell - slip)) * 100 * width_lots
        pnl = credit + exit_cash

        trades.append(dict(t=t, S=S, S2=S2, rv=r,
                           credit=credit, max_loss=max_loss,
                           pnl=pnl, ret_on_risk=pnl / max_loss * 100,
                           move=(S2 / S - 1) * 100))
        t += hold

    if len(trades) < 6:
        return None
    tr = pd.DataFrame(trades)
    return dict(sym=sym, n=len(tr),
                total=tr["pnl"].sum(),
                avg_credit=tr["credit"].mean(),
                avg_ror=tr["ret_on_risk"].mean(),
                winrate=(tr["pnl"] > 0).mean() * 100,
                best=tr["ret_on_risk"].max(),
                worst=tr["ret_on_risk"].min(),
                max_move=tr["move"].max(),
                avg_move=tr["move"].mean(),
                trades=tr)


# ---------------- 纯裸卖 CALL（对照） ----------------
def backtest_naked_call(sym, sell_otm=0.15, dte=30, hold=30, slip=0.01):
    df = get_df(sym)
    if df is None or len(df) < dte + hold + 40:
        return None
    kind = opt.classify(sym)
    close = df["close"].values
    rv = df["rv20"].values
    n = len(df)
    trades = []
    t = 30
    while t + hold < n:
        r = rv[t]
        if not (r == r) or r <= 0:
            t += hold
            continue
        S = close[t]
        K = round(S * (1 + sell_otm), 2)
        c_px, _ = opt.price_call(S, K, dte / 365, r, kind)
        if c_px <= 0.02:
            t += hold
            continue
        credit = (c_px - slip) * 100 - 2.0
        S2 = close[t + hold]
        r2 = rv[min(t + hold, n - 1)]
        if not (r2 == r2) or r2 <= 0:
            r2 = r
        T_out = max(0, dte - hold) / 365
        v, _ = opt.price_call(S2, K, T_out, r2, kind)
        pnl = credit - v * 100
        trades.append(dict(pnl=pnl, ret=pnl / (S * 100) * 100,
                           move=(S2 / S - 1) * 100))
        t += hold
    if len(trades) < 6:
        return None
    tr = pd.DataFrame(trades)
    return dict(sym=sym, n=len(tr), total=tr["pnl"].sum(),
                avg=tr["ret"].mean(),
                winrate=(tr["pnl"] > 0).mean() * 100,
                worst=tr["ret"].min(), max_move=tr["move"].max())


def main():
    stamp = datetime.now().strftime("%Y%m%d")
    p(f"熊市看涨价差回测 | {datetime.now():%Y-%m-%d %H:%M}\n")

    # ========== 主表：卖 15% / 买 35% ==========
    p("=" * 104)
    p("① 熊市看涨价差（卖 15% OTM call，买 35% OTM 保护，30天）")
    p("=" * 104)
    p(f"\n{'标的':>7}{'说明':>16}{'交易':>6}{'平均保证金':>11}"
      f"{'总盈亏$':>11}{'风险回报%':>10}{'胜率':>7}{'最好%':>8}{'最差%':>8}")
    rows = []
    for sym, desc in DECAY_TARGETS.items():
        r = backtest_bear_call(sym, sell_otm=0.15, buy_otm=0.35)
        if not r:
            p(f"{sym:>7}{desc:>16}  数据不足")
            continue
        p(f"{sym:>7}{desc:>16}{r['n']:>6}{r['avg_credit']:>+10,.0f}"
          f"{r['total']:>+11,.0f}{r['avg_ror']:>+9.1f}%"
          f"{r['winrate']:>6.0f}%{r['best']:>+7.0f}%{r['worst']:>+7.0f}%")
        rows.append(r)

    # ========== 对比：裸卖 vs 价差 ==========
    p("\n" + "=" * 104)
    p("② 裸卖 CALL vs 熊市价差（对比保护的价值）")
    p("=" * 104)
    p(f"\n{'标的':>7}{'裸卖总盈亏':>12}{'裸卖胜率':>10}{'裸卖最差%':>11}"
      f"{'价差总盈亏':>12}{'价差胜率':>10}{'价差最差%':>11}")
    for sym in ("SOXS", "SQQQ", "UVIX", "UVXY"):
        nk = backtest_naked_call(sym, 0.15)
        sp = backtest_bear_call(sym, 0.15, 0.35)
        if not nk or not sp:
            continue
        p(f"{sym:>7}{nk['total']:>+12,.0f}{nk['winrate']:>9.0f}%"
          f"{nk['worst']:>10.1f}%{sp['total']:>+12,.0f}"
          f"{sp['winrate']:>9.0f}%{sp['worst']:>10.1f}%")

    # ========== 行权价扫描 ==========
    p("\n" + "=" * 104)
    p("③ 行权价扫描（SOXS / UVXY）")
    p("=" * 104)
    p(f"\n{'标的':>7}{'卖':>6}{'买':>6}{'交易':>6}{'总盈亏$':>11}"
      f"{'风险回报%':>10}{'胜率':>7}{'最差%':>8}")
    for sym in ("SOXS", "UVXY"):
        for so, bo in [(0.10, 0.25), (0.15, 0.30), (0.15, 0.35),
                       (0.20, 0.40), (0.20, 0.50), (0.25, 0.50)]:
            r = backtest_bear_call(sym, so, bo)
            if not r:
                continue
            p(f"{sym:>7}{so*100:>5.0f}%{bo*100:>5.0f}%{r['n']:>6}"
              f"{r['total']:>+11,.0f}{r['avg_ror']:>+9.1f}%"
              f"{r['winrate']:>6.0f}%{r['worst']:>+7.0f}%")

    # ========== IV 过滤 ==========
    p("\n" + "=" * 104)
    p("④ IV 过滤（只在波动率高位卖）")
    p("=" * 104)

    def high_iv(rv, t, win=252, q=0.6):
        if t < win:
            return False
        hist = rv[t - win:t]
        hist = hist[hist == hist]
        if len(hist) < 60:
            return False
        return rv[t] >= np.quantile(hist, q)

    p(f"\n{'标的':>7}{'无过滤盈亏':>13}{'无过滤胜率':>12}"
      f"{'IV高位盈亏':>13}{'IV高位胜率':>12}")
    for sym in ("SOXS", "SQQQ", "UVXY", "VXX"):
        a = backtest_bear_call(sym, 0.15, 0.35)
        b = backtest_bear_call(sym, 0.15, 0.35, iv_rank_filter=high_iv)
        if not a:
            continue
        bs = f"{b['total']:>+13,.0f}{b['winrate']:>11.0f}%" if b else "       数据不足"
        p(f"{sym:>7}{a['total']:>+13,.0f}{a['winrate']:>11.0f}%{bs}")

    # ========== CSV ==========
    if rows:
        csv_path = os.path.join(config.DATA_DIR, f"bear_call_{stamp}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "sym", "n", "total", "avg_credit", "avg_ror",
                "winrate", "best", "worst", "avg_move", "max_move"],
                extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        p(f"\n✅ CSV: {csv_path}")

    # ========== HTML ==========
    html = build_html(rows, stamp)
    html_path = os.path.join(config.REPORT_DIR, f"bear_call_{stamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    latest = os.path.join(config.REPORT_DIR, "bear_call_latest.html")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)
    p(f"✅ HTML: {html_path}")

    with open(os.path.join(config.DATA_DIR, "bear_call.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
     margin:0;padding:24px;background:#f7f8fa;color:#1a1a1a}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:16px;margin:28px 0 10px;padding-bottom:5px;border-bottom:2px solid #e4e7ec}
.meta{color:#666;font-size:13px;margin-bottom:20px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;
      box-shadow:0 1px 3px rgba(0,0,0,.06);border-radius:6px;overflow:hidden}
th{background:#f0f2f5;text-align:left;padding:8px 10px;cursor:pointer}
td{padding:7px 10px;border-bottom:1px solid #f0f0f0}
.up{color:#d93025;font-weight:600}.dn{color:#1e8e3e;font-weight:600}
.card{background:#fff;padding:14px 18px;border-radius:6px;margin:10px 0;
      box-shadow:0 1px 3px rgba(0,0,0,.06)}
.note{font-size:12px;color:#666;margin:6px 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
"""

SORT = """
document.querySelectorAll('th').forEach(function(th,i){
  th.onclick=function(){var t=th.closest('table'),tb=t.querySelector('tbody');
  var rs=Array.from(tb.querySelectorAll('tr'));
  var asc=th.dataset.a!=='1';th.dataset.a=asc?'1':'0';
  rs.sort(function(a,b){var x=parseFloat(a.children[i].innerText.replace(/[,%$+]/g,''))||0,
  y=parseFloat(b.children[i].innerText.replace(/[,%$+]/g,''))||0;
  return asc?x-y:y-x});rs.forEach(function(r){tb.appendChild(r)})}});
"""


def build_html(rows, stamp):
    h = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         f"<title>熊市看涨价差回测 {stamp}</title>",
         f"<style>{CSS}</style></head><body><div class='wrap'>",
         "<h1>熊市看涨价差回测</h1>",
         f"<div class='meta'>{datetime.now():%Y-%m-%d %H:%M} · "
         "策略：卖 15% OTM call + 买 35% OTM 保护 call · 30 天滚动 · "
         "5 年数据 · BS 合成定价</div>"]

    h.append("<div class='grid'>")
    for r in rows:
        color = "#1e8e3e" if r["total"] > 0 else "#d93025"
        h.append(f"<div class='card'>"
                 f"<div style='font-size:15px;font-weight:600'>{r['sym']}</div>"
                 f"<div style='font-size:11px;color:#888'>{DECAY_TARGETS.get(r['sym'],'')}</div>"
                 f"<div style='font-size:22px;font-weight:700;color:{color};margin-top:4px'>"
                 f"{r['total']:+,.0f}</div>"
                 f"<div style='font-size:12px;color:#666;margin-top:4px'>"
                 f"胜率 {r['winrate']:.0f}% · 风险回报 {r['avg_ror']:+.0f}% · "
                 f"最差 {r['worst']:+.0f}%</div></div>")
    h.append("</div>")

    h.append("<h2>详细数据</h2>")
    h.append("<table><thead><tr><th>标的</th><th>说明</th><th>交易数</th>"
             "<th>平均保证金$</th><th>总盈亏$</th><th>风险回报%</th>"
             "<th>胜率%</th><th>最好%</th><th>最差%</th><th>平均标的变动%</th>"
             "</tr></thead><tbody>")
    for r in sorted(rows, key=lambda x: -x["total"]):
        cls = "up" if r["total"] > 0 else "dn"
        h.append(f"<tr><td><b>{r['sym']}</b></td>"
                 f"<td>{DECAY_TARGETS.get(r['sym'],'')}</td>"
                 f"<td>{r['n']}</td><td>{r['avg_credit']:,.0f}</td>"
                 f"<td class='{cls}'>{r['total']:+,.0f}</td>"
                 f"<td>{r['avg_ror']:+.1f}</td><td>{r['winrate']:.0f}</td>"
                 f"<td>{r['best']:+.0f}</td><td>{r['worst']:+.0f}</td>"
                 f"<td>{r['avg_move']:+.1f}</td></tr>")
    h.append("</tbody></table>")

    # 总盈亏柱状图
    if rows:
        h.append("<h2>总盈亏对比</h2><div class='card'>")
        h.append(charts.bar_svg(
            [dict(label=r["sym"], value=r["total"]) for r in rows],
            width=900, height=200))
        h.append("</div>")

    # 胜率柱状图
    if rows:
        h.append("<h2>胜率对比</h2><div class='card'>")
        h.append(charts.bar_svg(
            [dict(label=r["sym"], value=r["winrate"] - 50) for r in rows],
            width=900, height=180, fmt="{:+.0f}"))
        h.append("<div class='note'>纵轴以 50% 为基准，柱高 = 胜率 − 50%</div></div>")

    h.append("<div class='note' style='margin-top:24px'>"
             "说明：合成定价基于 BS + 实现波动率 + put/call 偏斜，"
             "实际成交价会因流动性差异。历史回测不代表未来。</div>")
    h.append(f"<script>{SORT}</script></div></body></html>")
    return "\n".join(h)


if __name__ == "__main__":
    main()
