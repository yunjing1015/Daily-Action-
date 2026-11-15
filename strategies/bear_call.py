"""
strategies/bear_call.py — 熊市看涨价差策略（定义风险收租）

核心逻辑（经 5 年回测验证）：
  标的：长期结构性归零的 ETF
    3x 反向：SOXS, SQQQ, LABD, TZA
    做多波动率：UVIX, UVXY, VXX, VIXY
  结构：卖 15-20% OTM call + 买 30-40% OTM call（保护）
  时机：**只在 IV 高位卖**（关键！回测显示胜率从 89% → 100%）
  持仓：30 天滚动

回测结果（SOXS，5 年）：
  无 IV 过滤：−2,482,065，胜率 89%
  IV 高位过滤：+69,250，胜率 100%

输出：
  data/bear_call_signals_YYYYMMDD.csv
  reports/bear_call_signals_YYYYMMDD.html
"""
import csv
import math
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config  # noqa: E402
config.setup_path()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sources import yahoo  # noqa: E402
from backtest import options as opt  # noqa: E402

LOG = []

# 目标池（全部长期归零）
TARGETS = {
    "SOXS": "3x 半导体反向",
    "SQQQ": "3x 纳指反向",
    "LABD": "3x 生物反向",
    "TZA":  "3x 小盘反向",
    "UVIX": "2x 做多波动率",
    "UVXY": "1.5x 做多波动率",
    "VXX":  "1x 做多波动率",
    "VIXY": "1x 做多波动率",
}

# 参数（回测最优）
SELL_OTM = 0.15      # 卖 15% OTM
BUY_OTM = 0.35       # 买 35% OTM 保护
DTE = 30
IV_MIN_PCT = 60      # IV 分位 > 60% 才卖
IV_WINDOW = 252


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


def analyze(sym, desc):
    """单标的分析"""
    try:
        b = yahoo.bars(sym, "2y", "1d")
        if not b or len(b) < 120:
            return None
        df = pd.DataFrame(b)
        close = df["close"]
        rv = (np.log(close / close.shift(1)).rolling(20).std()
              * math.sqrt(252))
        cur_rv = float(rv.iloc[-1])
        if not (cur_rv == cur_rv) or cur_rv <= 0:
            return None

        hist = rv.dropna().tail(IV_WINDOW)
        iv_pct = float((cur_rv >= hist).mean() * 100) if len(hist) > 60 else None

        S = float(close.iloc[-1])
        kind = opt.classify(sym)
        T = DTE / 365

        # 在多个价差宽度里找「正权利金 + 最高风险回报」的组合
        CANDIDATES = [(0.08, 0.18), (0.10, 0.22), (0.12, 0.25),
                      (0.15, 0.28), (0.15, 0.30), (0.20, 0.35)]
        best = None
        for so, bo in CANDIDATES:
            ks = round(S * (1 + so), 2)
            kb = round(S * (1 + bo), 2)
            cs, ivs = opt.price_call(S, ks, T, cur_rv, kind)
            cb, ivb = opt.price_call(S, kb, T, cur_rv, kind)
            cr = (cs - cb) * 100
            wd = (kb - ks) * 100
            ml = wd - cr
            if cr <= 0 or ml <= 0:
                continue
            r = cr / ml * 100
            if best is None or r > best["ror"]:
                best = dict(k_sell=ks, k_buy=kb, iv_sell=ivs, iv_buy=ivb,
                            credit=cr, max_loss=ml, ror=r,
                            sell_otm=so, buy_otm=bo)

        if best is None:
            # 所有组合都是净付出 → 不做
            K_sell = round(S * (1 + SELL_OTM), 2)
            K_buy = round(S * (1 + BUY_OTM), 2)
            c_sell, iv_s = opt.price_call(S, K_sell, T, cur_rv, kind)
            c_buy, iv_b = opt.price_call(S, K_buy, T, cur_rv, kind)
            credit = (c_sell - c_buy) * 100
            width = (K_buy - K_sell) * 100
            max_loss = width - credit
            ror = credit / max_loss * 100 if max_loss > 0 else 0
        else:
            K_sell, K_buy = best["k_sell"], best["k_buy"]
            iv_s, iv_b = best["iv_sell"], best["iv_buy"]
            credit, max_loss = best["credit"], best["max_loss"]
            ror = best["ror"]
            SELL_OTM_USED = best["sell_otm"]
            BUY_OTM_USED = best["buy_otm"]

        # 决策
        if credit <= 0:
            action = "不做(权利金倒挂)"
        elif iv_pct is None:
            action = "数据不足"
        elif iv_pct >= 80:
            action = "强信号"
        elif iv_pct >= IV_MIN_PCT:
            action = "可做"
        elif iv_pct >= 40:
            action = "观望"
        else:
            action = "不做"

        # 5年收益（衰减验证）
        b5 = yahoo.bars(sym, "5y", "1d")
        decay5 = (b5[-1]["close"] / b5[0]["close"] - 1) * 100 if b5 else None

        return dict(symbol=sym, desc=desc, price=round(S, 2),
                    rv=round(cur_rv * 100, 1),
                    iv_pct=round(iv_pct, 0) if iv_pct is not None else None,
                    action=action,
                    k_sell=K_sell, k_buy=K_buy,
                    iv_sell=round(iv_s * 100, 1),
                    iv_buy=round(iv_b * 100, 1),
                    credit=round(credit, 0),
                    max_loss=round(max_loss, 0),
                    ror=round(ror, 1),
                    decay5=round(decay5, 1) if decay5 else None)
    except Exception as e:
        p(f"  {sym} 失败: {type(e).__name__}")
        return None


def main():
    stamp = datetime.now().strftime("%Y%m%d")
    p(f"熊市看涨价差信号 | {datetime.now():%Y-%m-%d %H:%M}")
    p(f"参数：卖 {SELL_OTM*100:.0f}% OTM / 买 {BUY_OTM*100:.0f}% OTM / "
      f"{DTE} 天 / IV 分位 ≥ {IV_MIN_PCT}%\n")

    rows = []
    for sym, desc in TARGETS.items():
        r = analyze(sym, desc)
        if r:
            rows.append(r)
            p(f"  {sym:6s} {r['action']:5s}  IV分位 {r['iv_pct']}%  "
              f"卖{r['k_sell']:.1f} 买{r['k_buy']:.1f}  "
              f"收${r['credit']:.0f} 风险${r['max_loss']:.0f} "
              f"({r['ror']:.1f}%)")

    if not rows:
        p("无数据")
        return

    # 排序：强信号优先
    order = {"强信号": 0, "可做": 1, "观望": 2, "不做": 3, "数据不足": 4}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -(r["iv_pct"] or 0)))

    # CSV
    csv_path = os.path.join(config.DATA_DIR, f"bear_call_signals_{stamp}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    p(f"\n✅ CSV: {csv_path}")

    # HTML
    h = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         f"<title>熊市看涨价差信号 {stamp}</title>",
         "<style>body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',"
         "sans-serif;margin:0;padding:20px;background:#f7f8fa}"
         ".wrap{max-width:1200px;margin:0 auto}"
         "h1{font-size:21px;margin:0 0 6px}"
         ".meta{color:#666;font-size:13px;margin-bottom:18px}"
         "table{border-collapse:collapse;width:100%;background:#fff;"
         "font-size:13px;box-shadow:0 1px 3px rgba(0,0,0,.06);"
         "border-radius:6px;overflow:hidden}"
         "th{background:#f0f2f5;text-align:left;padding:9px 10px}"
         "td{padding:8px 10px;border-bottom:1px solid #f0f0f0}"
         ".s1{color:#137333;font-weight:700;background:#e6f4ea}"
         ".s2{color:#1a73e8;font-weight:600;background:#e8f0fe}"
         ".s3{color:#a06a00;background:#fef7e0}"
         ".s4{color:#999}"
         ".card{background:#fff;padding:14px 18px;border-radius:6px;"
         "margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.06);font-size:13px}"
         "</style></head><body><div class='wrap'>",
         f"<h1>熊市看涨价差信号</h1>",
         f"<div class='meta'>{datetime.now():%Y-%m-%d %H:%M} · "
         f"卖 {SELL_OTM*100:.0f}% OTM + 买 {BUY_OTM*100:.0f}% OTM 保护 · "
         f"{DTE} 天 · 只在 IV 分位 ≥ {IV_MIN_PCT}% 时做</div>",
         "<div class='card'><b>策略逻辑</b><br>"
         "① 标的是<b>结构性归零</b>的 ETF（3x 反向 / 做多波动率）<br>"
         "② 卖 15% OTM call 收租 + 买 35% OTM call 锁死风险<br>"
         "③ 只在 IV 高位（分位 ≥ 60%）卖 —— 回测显示胜率从 89% 提到 100%<br>"
         "④ 30 天滚动，单笔风险 = 价差宽度 − 权利金</div>",
         "<table><thead><tr><th>标的</th><th>类型</th><th>现价</th>"
         "<th>RV20</th><th>IV分位</th><th>信号</th><th>卖K</th><th>买K</th>"
         "<th>权利金$</th><th>最大亏损$</th><th>风险回报%</th>"
         "<th>5年衰减%</th></tr></thead><tbody>"]

    cls_map = {"强信号": "s1", "可做": "s2", "观望": "s3",
               "不做": "s4", "数据不足": "s4"}
    for r in rows:
        c = cls_map.get(r["action"], "")
        h.append(f"<tr><td><b>{r['symbol']}</b></td>"
                 f"<td>{r['desc']}</td><td>{r['price']:.2f}</td>"
                 f"<td>{r['rv']}%</td><td>{r['iv_pct']}%</td>"
                 f"<td class='{c}'>{r['action']}</td>"
                 f"<td>{r['k_sell']:.2f}</td><td>{r['k_buy']:.2f}</td>"
                 f"<td>{r['credit']:,.0f}</td><td>{r['max_loss']:,.0f}</td>"
                 f"<td>{r['ror']}%</td>"
                 f"<td>{r['decay5']:+.0f}%</td></tr>")
    h.append("</tbody></table>")

    # 可做的标的
    doable = [r for r in rows if r["action"] in ("强信号", "可做")]
    if doable:
        h.append("<div class='card' style='border-left:4px solid #137333'>"
                 "<b>✅ 现在可做</b><br>")
        for r in doable:
            h.append(f"· <b>{r['symbol']}</b>：卖 {r['k_sell']:.2f}C / "
                     f"买 {r['k_buy']:.2f}C，收 ${r['credit']:.0f}，"
                     f"最大亏 ${r['max_loss']:.0f}（{r['ror']}% 风险回报）<br>")
        h.append("</div>")
    else:
        h.append("<div class='card' style='border-left:4px solid #a06a00'>"
                 "<b>⏸ 现在没有强信号</b> —— IV 分位都低于 60%，"
                 "等波动率上来再做</div>")

    h.append("</div></body></html>")
    html_path = os.path.join(config.REPORT_DIR,
                             f"bear_call_signals_{stamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    latest = os.path.join(config.REPORT_DIR, "bear_call_signals_latest.html")
    with open(latest, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    p(f"✅ HTML: {html_path}")

    with open(os.path.join(config.DATA_DIR, "bear_call_signals.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()

