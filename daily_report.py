#!/usr/bin/env python3
"""
daily_report.py — 每日收盘波动率 & Gamma 结构报告

标的：
  核心：SPY, QQQ, SMH, SOXX（Gamma Wall / 攻防 / 成交量）
  衍生：VIX 系列、VXX, SVIX, UVXY, VIXY（跟随波动）

输出：
  reports/daily_YYYYMMDD.md      Markdown 报告
  reports/daily_YYYYMMDD.html    HTML 报告
  data/gex_history.csv           GEX 历史（复用 gex_history 逻辑）

只用标准库 + CBOE，不依赖富途/IBKR，可在 GitHub Actions 运行。
"""
import csv
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPORTS = os.path.join(HERE, "reports")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

CORE = ["SPY", "QQQ", "SMH", "SOXX"]
DERIV = ["VXX", "SVIX", "UVXY", "VIXY"]
if len(sys.argv) > 1:
    CORE = sys.argv[1].split(",")
    DERIV = []

os.makedirs(DATA, exist_ok=True)
os.makedirs(REPORTS, exist_ok=True)

LOG = []


def log(s=""):
    LOG.append(str(s))
    print(s, flush=True)


# ---------------- 抓取 ----------------
def fetch_symbol(sym, tries=2, timeout=45):
    url = f"https://cdn-api.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            log(f"  {sym} 第 {i+1} 次失败: {type(e).__name__}: {str(e)[:50]}")
            time.sleep(3)
    return None


def parse_chain(sym, raw):
    """返回 (spot, contracts)"""
    d = raw.get("data", raw)
    px = d.get("current_price")
    opts = d.get("options", []) or []
    rows = []
    today = datetime.now().date()
    for o in opts:
        s = o.get("option", "")
        try:
            body = s[len(sym):]
            yy, mm, dd = int(body[0:2]), int(body[2:4]), int(body[4:6])
            right = body[6]
            strike = int(body[7:]) / 1000.0
            exp = datetime(2000 + yy, mm, dd).date()
        except Exception:
            continue
        dte = (exp - today).days
        if dte < 0 or dte > 60:
            continue
        rows.append(dict(
            exp=exp.isoformat(), dte=dte, right=right, strike=strike,
            oi=float(o.get("open_interest") or 0),
            vol=float(o.get("volume") or 0),
            iv=float(o.get("iv") or 0),
            gamma=float(o.get("gamma") or 0),
            delta=float(o.get("delta") or 0),
            bid=float(o.get("bid") or 0), ask=float(o.get("ask") or 0)))
    return px, rows


def implied_spot(rows, fallback):
    if not rows:
        return fallback
    e = min(r["exp"] for r in rows)
    byk = defaultdict(dict)
    for r in rows:
        if r["exp"] == e and r["bid"] > 0 and r["ask"] > 0:
            byk[r["strike"]][r["right"]] = (r["bid"] + r["ask"]) / 2
    cand = []
    for k, v in byk.items():
        if "C" in v and "P" in v:
            cand.append((abs(k - fallback), k + (v["C"] - v["P"])))
    if not cand:
        return fallback
    cand.sort()
    return sum(s for _, s in cand[:5]) / min(5, len(cand))


# ---------------- 计算 ----------------
def gex_by_expiry(rows, S):
    """按到期算 GEX / flip / walls / max neg"""
    exps = sorted({r["exp"] for r in rows})
    out = []
    for e in exps:
        sub = [r for r in rows if r["exp"] == e]
        gex_k = defaultdict(float)
        oi_c, oi_p = defaultdict(float), defaultdict(float)
        ivs = []
        vc = vp = 0.0
        for r in sub:
            vc += r["vol"] if r["right"] == "C" else 0
            vp += r["vol"] if r["right"] == "P" else 0
            if not r["gamma"] or not r["oi"]:
                continue
            v = r["gamma"] * r["oi"] * 100 * S * S * 0.01
            if r["right"] == "C":
                gex_k[r["strike"]] += v
                oi_c[r["strike"]] += r["oi"]
            else:
                gex_k[r["strike"]] -= v
                oi_p[r["strike"]] += r["oi"]
        if not gex_k:
            continue
        # ATM IV
        for r in sub:
            if r["iv"] > 0 and abs(r["strike"] / S - 1) <= 0.02:
                ivs.append(r["iv"])
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
        out.append(dict(
            exp=e, dte=sub[0]["dte"], total=total, flip=flip,
            cw=cw, pw=pw, maxneg_k=maxneg_k, maxneg=gex_k[maxneg_k],
            oi_c=dict(oi_c), oi_p=dict(oi_p),
            atm_iv=(statistics.median(ivs) if ivs else None),
            vol_c=vc, vol_p=vp, n=len(sub),
            gex_k=dict(gex_k)))
    return out


def vol_profile(rows, S):
    """成交量分析"""
    vc = defaultdict(float)
    vp = defaultdict(float)
    for r in rows:
        if r["dte"] > 7:
            continue
        (vc if r["right"] == "C" else vp)[r["strike"]] += r["vol"]
    tot_c = sum(vc.values())
    tot_p = sum(vp.values())
    top = sorted(((k, vc[k] + vp[k]) for k in set(vc) | set(vp)),
                 key=lambda x: -x[1])[:8]
    return dict(pcr=(tot_p / tot_c if tot_c else None),
                vol_c=tot_c, vol_p=tot_p,
                top=top, vc=vc, vp=vp)


# ---------------- 报告 ----------------
def main():
    stamp = datetime.now()
    date_s = stamp.strftime("%Y-%m-%d")
    log(f"=== 每日报告 {stamp:%Y-%m-%d %H:%M} ===\n")

    results = {}
    all_syms = CORE + DERIV
    for sym in all_syms:
        log(f"[{sym}] 抓取...")
        raw = fetch_symbol(sym)
        if raw is None:
            log(f"  {sym} 抓取失败，跳过\n")
            continue
        px, rows = parse_chain(sym, raw)
        if not rows:
            log(f"  {sym} 无有效合约，跳过\n")
            continue
        S = implied_spot(rows, px)
        gex = gex_by_expiry(rows, S)
        vol = vol_profile(rows, S)
        results[sym] = dict(spot=S, raw=px, rows=rows, gex=gex, vol=vol,
                            n=len(rows))
        log(f"  现货 {S:.2f}  合约 {len(rows)}  到期 {len(gex)}\n")

    if not results:
        log("全部失败，退出")
        sys.exit(1)

    # ---- 写 markdown ----
    L = []
    L.append(f"# 每日波动率 & Gamma 结构报告")
    L.append("")
    L.append(f"**{date_s}**  ·  生成 {stamp:%H:%M} (本地)  ·  "
             f"数据源 CBOE（延迟 15 分钟）")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一、市场概览")
    L.append("")
    L.append("| 标的 | 现货 | 0DTE GEX(M) | Gamma Flip | Call 墙 | Put 墙 | 最大负 gamma | ATM IV |")
    L.append("|---|---|---|---|---|---|---|---|")
    for sym in all_syms:
        if sym not in results:
            L.append(f"| {sym} | 抓取失败 | — | — | — | — | — | — |")
            continue
        r = results[sym]
        g0 = next((g for g in r["gex"] if g["dte"] == 0), None)
        if g0 is None and r["gex"]:
            g0 = r["gex"][0]
        if g0:
            L.append(f"| **{sym}** | {r['spot']:.2f} | {g0['total']/1e6:+.0f} | "
                     f"{g0['flip'] or '—'} | {g0['cw'] or '—'} | {g0['pw'] or '—'} | "
                     f"{g0['maxneg_k']:.0f} ({g0['maxneg']/1e6:+.0f}M) | "
                     f"{(g0['atm_iv']*100 if g0['atm_iv'] else 0):.0f}% |")
        else:
            L.append(f"| **{sym}** | {r['spot']:.2f} | — | — | — | — | — | — |")
    L.append("")

    # ---- 核心标的详情 ----
    L.append("## 二、核心标的攻防结构")
    for sym in CORE:
        if sym not in results:
            continue
        r = results[sym]
        S = r["spot"]
        L.append("")
        L.append(f"### {sym}  ·  现货 {S:.2f}")
        L.append("")
        L.append("| 到期 | DTE | 总 GEX(M) | Flip | Call 墙 | Put 墙 | 最大负 gamma | ATM IV |")
        L.append("|---|---|---|---|---|---|---|---|")
        for g in r["gex"][:6]:
            L.append(f"| {g['exp']} | {g['dte']} | {g['total']/1e6:+.0f} | "
                     f"{g['flip'] or '—'} | {g['cw'] or '—'} | {g['pw'] or '—'} | "
                     f"{g['maxneg_k']:.0f} ({g['maxneg']/1e6:+.0f}M) | "
                     f"{(g['atm_iv']*100 if g['atm_iv'] else 0):.0f}% |")
        # 关键判断
        g0 = next((g for g in r["gex"] if g["dte"] == 0), r["gex"][0] if r["gex"] else None)
        if g0:
            L.append("")
            neg = g0["total"] < 0
            L.append(f"- **体制**：{'负 GEX（波动放大，做市商追涨杀跌）' if neg else '正 GEX（波动收敛，做市商均值回归）'}")
            if g0["flip"]:
                L.append(f"- **Gamma Flip**：{g0['flip']} （跌破则进入负 gamma 区）")
            else:
                L.append(f"- **无 flip**（全区间负 gamma），现价 {S:.2f} 已在负 gamma 区")
            L.append(f"- **最危险位置**：{g0['maxneg_k']:.0f} 有 {g0['maxneg']/1e6:+.0f}M 负 gamma")
            if g0["cw"]:
                L.append(f"- **上方阻力**：{g0['cw']:.0f}（Call 墙）")
            if g0["pw"]:
                L.append(f"- **下方支撑**：{g0['pw']:.0f}（Put 墙）")

    # ---- 成交量 ----
    L.append("")
    L.append("## 三、成交量分析（近 7 日到期）")
    L.append("")
    L.append("| 标的 | Call 量 | Put 量 | P/C | 最活跃行权价 |")
    L.append("|---|---|---|---|---|")
    for sym in all_syms:
        if sym not in results:
            continue
        v = results[sym]["vol"]
        top = ", ".join(f"{k:.0f}({int(n):,})" for k, n in v["top"][:4])
        pcr = f"{v['pcr']:.2f}" if v["pcr"] else "—"
        L.append(f"| **{sym}** | {int(v['vol_c']):,} | {int(v['vol_p']):,} | "
                 f"{pcr} | {top} |")
    L.append("")
    L.append("> P/C > 1.2 偏空（put 活跃）；< 0.6 偏多（call 活跃）")

    # ---- 波动率 ----
    L.append("")
    L.append("## 四、波动率结构")
    L.append("")
    L.append("| 标的 | 0DTE IV | 近月 IV | 远月 IV | 期限结构 |")
    L.append("|---|---|---|---|---|")
    for sym in all_syms:
        if sym not in results:
            continue
        gs = [g for g in results[sym]["gex"] if g["atm_iv"]]
        if not gs:
            L.append(f"| {sym} | — | — | — | — |")
            continue
        iv0 = gs[0]["atm_iv"] * 100
        iv1 = gs[min(1, len(gs)-1)]["atm_iv"] * 100
        ivN = gs[-1]["atm_iv"] * 100
        shape = "正挂(远月高)" if ivN > iv1 * 1.05 else (
            "倒挂(近月高)" if iv1 > ivN * 1.05 else "平坦")
        L.append(f"| **{sym}** | {iv0:.1f}% | {iv1:.1f}% | {ivN:.1f}% | {shape} |")

    # ---- 衍生品跟随 ----
    L.append("")
    L.append("## 五、衍生品跟随（VIX 家族）")
    L.append("")
    got_any = False
    L.append("| 标的 | 现货 | 0DTE GEX | Put/Call 量 | ATM IV |")
    L.append("|---|---|---|---|---|")
    for sym in DERIV:
        if sym not in results:
            continue
        got_any = True
        r = results[sym]
        g0 = next((g for g in r["gex"] if g["dte"] == 0), r["gex"][0] if r["gex"] else None)
        v = r["vol"]
        pcr = f"{v['pcr']:.2f}" if v["pcr"] else "—"
        iv = f"{(g0['atm_iv']*100):.0f}%" if g0 and g0["atm_iv"] else "—"
        g = f"{g0['total']/1e6:+.0f}" if g0 else "—"
        L.append(f"| **{sym}** | {r['spot']:.2f} | {g} | {pcr} | {iv} |")
    if not got_any:
        L.append("| — | 无衍生品数据 | — | — | — |")

    # 交叉验证：VXX（做多 VIX 期货）vs SVIX（做空 VIX 期货）
    L.append("")
    L.append("### 交叉验证（VXX vs SVIX）")
    L.append("")
    vx = results.get("VXX")
    sx = results.get("SVIX")
    if vx and sx:
        pc_v = vx["vol"]["pcr"]
        pc_s = sx["vol"]["pcr"]
        L.append(f"- **VXX P/C**：{pc_v:.2f}（{'call 活跃 → 看涨波动率' if pc_v and pc_v < 0.8 else ('put 活跃 → 看跌波动率' if pc_v and pc_v > 1.2 else '中性')}）")
        L.append(f"- **SVIX P/C**：{pc_s:.2f}（{'call 活跃 → 看涨 SVIX → 看跌波动率' if pc_s and pc_s < 0.8 else ('put 活跃 → 看跌 SVIX → 看涨波动率' if pc_s and pc_s > 1.2 else '中性')}）")
        if pc_v and pc_s:
            if pc_v < 0.8 and pc_s > 1.2:
                L.append("- **✅ 两个信号一致：市场预期波动率上升**")
            elif pc_v > 1.2 and pc_s < 0.8:
                L.append("- **✅ 两个信号一致：市场预期波动率下降**")
            else:
                L.append("- **⚠️ 信号不一致，方向不明**")
        L.append("")
        L.append("> 说明：VXX 做多 VIX 期货，SVIX 做空 VIX 期货。两者期权流向应相反。")
        L.append("> 注意：SVIX/VXX 的期权 OI 较小，GEX 意义不大，重点看 P/C 和 IV。")

    # ---- 信号汇总 ----
    L.append("")
    L.append("## 六、交易信号")
    L.append("")
    for sym in CORE:
        if sym not in results:
            continue
        r = results[sym]
        S = r["spot"]
        g0 = next((g for g in r["gex"] if g["dte"] == 0), r["gex"][0] if r["gex"] else None)
        if not g0:
            continue
        sig = []
        if g0["total"] < -500e6:
            sig.append("🔴 强负 GEX")
        elif g0["total"] < 0:
            sig.append("🟠 负 GEX")
        elif g0["total"] > 500e6:
            sig.append("🟢 强正 GEX")
        else:
            sig.append("🟡 弱正 GEX")
        if g0["maxneg_k"] and S < g0["maxneg_k"]:
            sig.append(f"现价在最大负 gamma({g0['maxneg_k']:.0f})下方 → 加速风险")
        if g0["cw"] and S > g0["cw"]:
            sig.append(f"突破 Call 墙({g0['cw']:.0f}) → 上方无阻力")
        if g0["pw"] and S < g0["pw"]:
            sig.append(f"跌破 Put 墙({g0['pw']:.0f}) → 支撑失守")
        pcr = r["vol"]["pcr"]
        if pcr:
            if pcr > 1.2:
                sig.append(f"P/C {pcr:.2f} 偏空")
            elif pcr < 0.6:
                sig.append(f"P/C {pcr:.2f} 偏多")
        L.append(f"- **{sym}**（{S:.2f}）：{' · '.join(sig)}")

    # ---- 风险提示 ----
    L.append("")
    L.append("## 七、风险提示")
    L.append("")
    L.append("- **OI 是 T-1 的**：GEX = 昨日持仓 × 今日价格，盘中持仓变化不反映")
    L.append("- **GEX 符号是假设**：假定做市商持有 call 多头、put 空头，未经验证")
    L.append("- **CBOE 延迟 15 分钟**：不适合高频交易")
    L.append("- **负 GEX 不等于必跌**：只代表波动放大，方向仍看其他因素")
    L.append("- **Gamma 墙会移动**：盘中价格变化会改变墙位，需实时重算")
    L.append("")
    L.append("---")
    L.append(f"*数据源：CBOE 延迟报价 · 生成于 {stamp:%Y-%m-%d %H:%M:%S}*")

    md = "\n".join(L)

    # ---- 写文件 ----
    md_path = os.path.join(REPORTS, f"daily_{date_s}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    log(f"\n✅ Markdown → {md_path}")

    # 简单 HTML
    html = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<title>每日波动率报告</title>",
            "<style>body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;",
            "max-width:1100px;margin:24px auto;padding:0 16px;line-height:1.7;color:#222}",
            "table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}",
            "th,td{border:1px solid #ddd;padding:6px 10px;text-align:left}",
            "th{background:#f5f5f5}code{background:#f5f5f5;padding:2px 5px;border-radius:3px}",
            "h2{border-bottom:2px solid #eee;padding-bottom:6px;margin-top:32px}",
            "h3{color:#0a58ca;margin-top:24px}</style></head><body>"]
    in_table = False
    for line in md.split("\n"):
        if line.startswith("# "):
            html.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("### "):
            html.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            html.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                html.append("<table>")
                in_table = True
            tag = "th" if html[-1] == "<table>" else "td"
            html.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        else:
            if in_table:
                html.append("</table>")
                in_table = False
            if line.strip() == "---":
                html.append("<hr>")
            elif line.strip().startswith("- "):
                html.append(f"<li>{line.strip()[2:]}</li>")
            elif line.strip():
                html.append(f"<p>{line}</p>")
    if in_table:
        html.append("</table>")
    html.append("</body></html>")

    html_path = os.path.join(REPORTS, f"daily_{date_s}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    log(f"✅ HTML → {html_path}")

    # ---- 追加 GEX 历史 ----
    hist = os.path.join(DATA, "gex_history.csv")
    fields = ["date", "ts", "symbol", "spot", "spot_raw", "exp", "dte",
              "total_gex", "flip", "call_wall", "put_wall",
              "maxneg_k", "maxneg_gex", "call_oi_k", "put_oi_k", "n_contract"]
    existing = set()
    if os.path.exists(hist):
        with open(hist, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing.add((row["date"], row["symbol"], row["exp"]))
    new = []
    for sym, r in results.items():
        for g in r["gex"]:
            key = (date_s, sym, g["exp"])
            if key in existing:
                continue
            new.append(dict(
                date=date_s, ts=stamp.isoformat(timespec="seconds"), symbol=sym,
                spot=round(r["spot"], 2), spot_raw=r["raw"], exp=g["exp"], dte=g["dte"],
                total_gex=round(g["total"] / 1e6, 1), flip=g["flip"],
                call_wall=g["cw"], put_wall=g["pw"],
                maxneg_k=g["maxneg_k"], maxneg_gex=round(g["maxneg"] / 1e6, 1),
                call_oi_k=int(g["oi_c"].get(g["cw"], 0)),
                put_oi_k=int(g["oi_p"].get(g["pw"], 0)),
                n_contract=g["n"]))
    if new:
        write_header = not os.path.exists(hist)
        with open(hist, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if write_header:
                w.writeheader()
            w.writerows(new)
        log(f"✅ GEX 历史 +{len(new)} 条 → {hist}")

    with open(os.path.join(REPORTS, "last_run.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()



