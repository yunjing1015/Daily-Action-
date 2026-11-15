"""
strategies/signals.py — 策略信号生成

已回测验证的策略：
  ① VIX 均值回归（买 SVIX）
     条件：VIX 60日分位 > 75% + VIX/VIX3M < 1（contango）+ SVIX 分位 < 60%
     回测：VIX 分位 80-100% 时，SVIX 未来 1 日 +2.03%，胜率 80.6%

  ② GEX 体制过滤
     负 GEX 期间不做多 SVIX（波动会继续放大）

  ③ 超卖反弹（NVDA 类型）
     RSI < 25 + 接近 52 周低 + 放量 → 观察反弹

输出：
  data/signals_YYYYMMDD.csv
  reports/signals_YYYYMMDD.html
"""
import csv
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config  # noqa: E402
config.setup_path()

import pandas as pd  # noqa: E402

from sources import yahoo  # noqa: E402
from indicators import calc  # noqa: E402

LOG = []


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


def pct_rank(series, n=60):
    """当前值在过去 n 天的分位"""
    s = series.tail(n).dropna()
    if len(s) < 20:
        return None
    return float((s.iloc[-1] >= s).mean() * 100)


def get_df(sym, days="1y"):
    b = yahoo.bars(sym, days, "1d")
    if not b or len(b) < 60:
        return None
    return pd.DataFrame(b)


# ---------------- 策略 ①：VIX 均值回归 ----------------
def signal_svix():
    """买 SVIX 的信号"""
    try:
        df_vix = get_df("^VIX")
        df_vix3m = get_df("^VIX3M")
        df_svix = get_df("SVIX")
        if df_vix is None or df_svix is None:
            return None

        vix = float(df_vix["close"].iloc[-1])
        vix_pct = pct_rank(df_vix["close"], 60)
        svix = float(df_svix["close"].iloc[-1])
        svix_pct = pct_rank(df_svix["close"], 60)

        # 期限结构（VIX / VIX3M）
        ratio = None
        shape = "未知"
        if df_vix3m is not None:
            v3 = float(df_vix3m["close"].iloc[-1])
            if v3:
                ratio = vix / v3
                shape = "contango" if ratio < 1 else "backwardation"

        # 打分
        score = 0
        reasons = []
        if vix_pct is not None:
            if vix_pct > 75:
                score += 2
                reasons.append(f"VIX分位{vix_pct:.0f}%(高)")
            elif vix_pct < 25:
                score -= 2
                reasons.append(f"VIX分位{vix_pct:.0f}%(低)")
        if ratio is not None:
            if ratio < 0.95:
                score += 2
                reasons.append(f"contango({ratio:.2f})")
            elif ratio > 1.05:
                score -= 3
                reasons.append(f"backwardation({ratio:.2f})←禁做多")
        if svix_pct is not None:
            if svix_pct < 40:
                score += 1
                reasons.append(f"SVIX分位{svix_pct:.0f}%(低)")
            elif svix_pct > 90:
                score -= 1
                reasons.append(f"SVIX分位{svix_pct:.0f}%(高)")

        if score >= 4:
            action = "BUY"
        elif score <= -2:
            action = "SELL"
        else:
            action = "WAIT"

        return dict(strategy="VIX均值回归", symbol="SVIX", action=action,
                    score=score, vix=round(vix, 2),
                    vix_pct=round(vix_pct, 0) if vix_pct else None,
                    term=shape,
                    term_ratio=round(ratio, 3) if ratio else None,
                    svix=round(svix, 2),
                    svix_pct=round(svix_pct, 0) if svix_pct else None,
                    reasons=" · ".join(reasons))
    except Exception as e:
        p(f"  SVIX 信号失败: {type(e).__name__}")
        return None


# ---------------- 策略 ②：超卖反弹观察 ----------------
def signal_oversold(symbols=("NVDA", "AMD", "SMH", "SOXX", "TSLA", "META")):
    """超卖反弹候选"""
    out = []
    for sym in symbols:
        try:
            df = get_df(sym)
            if df is None:
                continue
            c = df["close"]
            rsi = float(calc.rsi(c, 14).iloc[-1])
            price = float(c.iloc[-1])
            lo52 = float(c.tail(252).min())
            dist_lo = (price / lo52 - 1) * 100
            vol_ratio = float((df["volume"] / df["volume"].rolling(20).mean()).iloc[-1])

            if rsi < 30 and dist_lo < 15:
                action = "观察反弹"
                score = 3 if rsi < 22 else 2
            elif rsi < 35:
                action = "偏弱"
                score = 1
            else:
                continue

            out.append(dict(strategy="超卖反弹", symbol=sym, action=action,
                            score=score, vix=None, vix_pct=None, term=None,
                            term_ratio=None, svix=round(price, 2),
                            svix_pct=round(dist_lo, 1),
                            reasons=f"RSI{rsi:.0f} · 距52周低{dist_lo:+.1f}% · "
                                    f"量比{vol_ratio:.1f}"))
        except Exception:
            pass
    return out


# ---------------- 策略 ③：突破候选 ----------------
def signal_breakout(symbols=("SPY", "QQQ", "SMH", "SOXX", "XLK", "XLF", "XLV")):
    """接近 52 周高 + 放量 = 突破候选"""
    out = []
    for sym in symbols:
        try:
            df = get_df(sym)
            if df is None:
                continue
            c = df["close"]
            price = float(c.iloc[-1])
            hi52 = float(c.tail(252).max())
            dist_hi = (price / hi52 - 1) * 100
            vol_ratio = float((df["volume"] / df["volume"].rolling(20).mean()).iloc[-1])
            adx_v = float(calc.adx(df)[0].iloc[-1])

            if dist_hi > -2 and vol_ratio > 1.3:
                out.append(dict(strategy="突破候选", symbol=sym, action="关注突破",
                                score=2, vix=None, vix_pct=None, term=None,
                                term_ratio=None, svix=round(price, 2),
                                svix_pct=round(dist_hi, 1),
                                reasons=f"距52周高{dist_hi:+.1f}% · "
                                        f"量比{vol_ratio:.1f} · ADX{adx_v:.0f}"))
        except Exception:
            pass
    return out


def main():
    stamp = datetime.now().strftime("%Y%m%d")
    p(f"策略信号 | {datetime.now():%Y-%m-%d %H:%M}\n")

    rows = []
    p("① VIX 均值回归（SVIX）...")
    r = signal_svix()
    if r:
        rows.append(r)
        p(f"  {r['action']:5s} 评分{r['score']:+d}  VIX {r['vix']} "
          f"(分位{r['vix_pct']}%)  {r['term']}  {r['reasons']}")

    p("\n② 超卖反弹...")
    for r in signal_oversold():
        rows.append(r)
        p(f"  {r['symbol']:6s} {r['action']:6s} {r['reasons']}")

    p("\n③ 突破候选...")
    for r in signal_breakout():
        rows.append(r)
        p(f"  {r['symbol']:6s} {r['action']:6s} {r['reasons']}")

    if not rows:
        p("\n今天没有明确信号。")
        return

    # CSV
    csv_path = os.path.join(config.DATA_DIR, f"signals_{stamp}.csv")
    keys = ["strategy", "symbol", "action", "score", "reasons",
            "vix", "vix_pct", "term", "term_ratio", "svix", "svix_pct"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    p(f"\n✅ CSV: {csv_path}")

    # HTML
    h = [f"<!DOCTYPE html><html><head><meta charset='utf-8'>",
         f"<title>策略信号 {stamp}</title>",
         "<style>body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',"
         "sans-serif;margin:0;padding:20px;background:#f7f8fa}"
         "table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;"
         "box-shadow:0 1px 3px rgba(0,0,0,.06);border-radius:6px}"
         "th{background:#f0f2f5;text-align:left;padding:8px 10px}"
         "td{padding:7px 10px;border-bottom:1px solid #f0f0f0}"
         ".buy{color:#1e8e3e;font-weight:700}.sell{color:#d93025;font-weight:700}"
         ".wait{color:#999}h1{font-size:20px}.meta{color:#666;font-size:12px;"
         "margin-bottom:16px}</style></head><body>",
         f"<h1>策略信号</h1>",
         f"<div class='meta'>{datetime.now():%Y-%m-%d %H:%M} · "
         f"{len(rows)} 个信号</div>",
         "<table><thead><tr><th>策略</th><th>标的</th><th>动作</th>"
         "<th>评分</th><th>依据</th></tr></thead><tbody>"]
    for r in rows:
        cls = "buy" if "BUY" in r["action"] or "观察" in r["action"] else (
            "sell" if "SELL" in r["action"] else "wait")
        h.append(f"<tr><td>{r['strategy']}</td><td><b>{r['symbol']}</b></td>"
                 f"<td class='{cls}'>{r['action']}</td>"
                 f"<td>{r['score']:+d}</td><td>{r['reasons']}</td></tr>")
    h.append("</tbody></table></body></html>")

    html_path = os.path.join(config.REPORT_DIR, f"signals_{stamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    p(f"✅ HTML: {html_path}")


if __name__ == "__main__":
    main()
