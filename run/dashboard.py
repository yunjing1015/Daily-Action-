#!/usr/bin/env python3
"""
run/dashboard.py — 生成每日量化看板（单文件 HTML）

产出：reports/dashboard_YYYYMMDD.html
  · 浏览器直接打开，无需服务器
  · 表格可点击排序
  · 颜色标记超买/超卖/放量
  · 自包含（无 CDN 依赖）

用法：
    python run/dashboard.py            # 生成今天的
    python run/dashboard.py --no-fetch # 只用数据库已有数据
"""
import os
import sys
import json
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config, db, universe  # noqa: E402
config.setup_path()

import pandas as pd  # noqa: E402

from indicators import calc  # noqa: E402

LOG = []


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


# ---------- 数据获取 ----------
def fetch_bars(sym, days=400):
    """从富途拉日线（缓存进 bars 表）"""
    code = universe.to_futu(sym)
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        from sources import futu as fs
        kl = fs.history(code, start=start, max_count=600)
        if kl is None or len(kl) == 0:
            return None
        kl = kl.copy()
        kl["date"] = pd.to_datetime(kl["time_key"]).dt.strftime("%Y-%m-%d")
        rows = [dict(symbol=sym, date=r["date"],
                     open=float(r["open"]), high=float(r["high"]),
                     low=float(r["low"]), close=float(r["close"]),
                     volume=int(r["volume"])) for _, r in kl.iterrows()]
        with db.connect() as conn:
            db.upsert(conn, "bars", rows)
        return kl
    except Exception as e:
        p(f"    {sym} 抓取失败: {type(e).__name__}")
        return None


def load_bars(sym):
    """从 DB 读日线"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM bars "
            "WHERE symbol=? ORDER BY date", (sym,)).fetchall()
    if len(rows) < 30:
        return None
    return pd.DataFrame([dict(r) for r in rows])


def fetch_snapshots(syms):
    """批量快照"""
    from sources import futu as fs
    codes = [universe.to_futu(s) for s in syms]
    rows = fs.snapshot(codes)
    out = {}
    for r in rows:
        sym = r["code"].replace("US..VIX", "VIX").replace("US.", "")
        px, tag = fs.best_price(r)
        out[sym] = dict(px=px, tag=tag,
                        prev=float(r.get("prev_close_price") or 0),
                        high=float(r.get("high_price") or 0),
                        low=float(r.get("low_price") or 0),
                        vol=float(r.get("volume") or 0),
                        amp=float(r.get("amplitude") or 0))
    return out


# ---------- HTML ----------
CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
     margin:0;padding:24px;background:#f7f8fa;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1500px;margin:0 auto}
h1{font-size:24px;margin:0 0 6px}
.meta{color:#666;font-size:13px;margin-bottom:24px}
h2{font-size:17px;margin:32px 0 12px;padding-bottom:6px;
   border-bottom:2px solid #e4e7ec}
table{border-collapse:collapse;width:100%;background:#fff;
      font-size:13px;box-shadow:0 1px 3px rgba(0,0,0,.06);border-radius:6px;
      overflow:hidden;margin-bottom:8px}
th{background:#f0f2f5;text-align:left;padding:9px 10px;font-weight:600;
   white-space:nowrap;cursor:pointer;user-select:none;border-bottom:1px solid #e4e7ec}
th:hover{background:#e6e9ee}
td{padding:8px 10px;border-bottom:1px solid #f0f0f0;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafbfc}
.up{color:#d93025;font-weight:600}
.dn{color:#1e8e3e;font-weight:600}
.mut{color:#999}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;
     margin-right:3px;background:#eef2f7;color:#445}
.warn{background:#fef7e0;color:#a06a00}
.bad{background:#fce8e6;color:#c5221f}
.good{background:#e6f4ea;color:#137333}
.note{font-size:12px;color:#666;margin:4px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:8px}
.card{background:#fff;padding:14px 16px;border-radius:6px;
      box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card .k{font-size:12px;color:#888}
.card .v{font-size:20px;font-weight:600;margin-top:2px}
"""

SORT_JS = """
document.querySelectorAll('table').forEach(function(t){
  var ths=t.querySelectorAll('th');
  ths.forEach(function(th,i){
    th.addEventListener('click',function(){
      var rows=Array.from(t.querySelectorAll('tbody tr'));
      var asc=th.dataset.asc!=='1'; th.dataset.asc=asc?'1':'0';
      rows.sort(function(a,b){
        var x=a.children[i].dataset.v, y=b.children[i].dataset.v;
        if(x===undefined){x=a.children[i].innerText;y=b.children[i].innerText;}
        var nx=parseFloat(x), ny=parseFloat(y);
        if(!isNaN(nx)&&!isNaN(ny)) return asc?nx-ny:ny-nx;
        return asc?String(x).localeCompare(String(y)):String(y).localeCompare(String(x));
      });
      rows.forEach(function(r){t.querySelector('tbody').appendChild(r)});
    });
  });
});
"""


def cls(val, good_high=True, hi=70, lo=30):
    if val is None:
        return ""
    if good_high:
        return "up" if val > hi else ("dn" if val < lo else "")
    return ""


def build(universe_list, snaps, inds, gex_rows, vix_curve):
    now = datetime.now()
    h = []
    h.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    h.append(f"<title>量化看板 {now:%Y-%m-%d}</title>")
    h.append(f"<style>{CSS}</style></head><body><div class='wrap'>")
    h.append(f"<h1>美股量化看板</h1>")
    h.append(f"<div class='meta'>生成时间 {now:%Y-%m-%d %H:%M} · "
             f"标的 {len(universe_list)} 个 · "
             f"数据源 富途(实时/延时) + CBOE(延迟15分) + 本地DB</div>")

    # ===== 概览卡片 =====
    core = [s for s in ("SPY", "QQQ", "SMH", "SOXX") if s in snaps]
    if core:
        h.append("<div class='grid'>")
        for s in core:
            sn = snaps[s]
            chg = (sn["px"] / sn["prev"] - 1) * 100 if sn["prev"] else 0
            c = "up" if chg > 0 else "dn"
            h.append(f"<div class='card'><div class='k'>{s}</div>"
                     f"<div class='v'>{sn['px']:.2f}</div>"
                     f"<div class='{c}' style='font-size:13px'>{chg:+.2f}% "
                     f"<span class='mut'>({sn['tag']})</span></div></div>")
        h.append("</div>")

    # ===== 六类指标 =====
    h.append("<h2>一、六类指标总表（点击表头排序）</h2>")
    h.append("<div class='note'>"
             "评分 = 趋势(+2) + RSI(±1)；红色=上涨/超买，绿色=下跌/超卖</div>")
    h.append("<table><thead><tr>"
             "<th>标的</th><th>分组</th><th>现价</th><th>涨跌%</th>"
             "<th>ADX</th><th>RSI14</th><th>Z20</th><th>ATR%</th>"
             "<th>RV20</th><th>PCT252</th><th>量比</th><th>评分</th><th>提示</th>"
             "</tr></thead><tbody>")
    for s in universe_list:
        sn = snaps.get(s, {})
        it = inds.get(s)
        if not sn or not it:
            h.append(f"<tr><td>{s}</td><td>{universe.group_of(s)}</td>"
                     f"<td colspan='11' class='mut'>数据不足</td></tr>")
            continue
        chg = (sn["px"] / sn["prev"] - 1) * 100 if sn["prev"] else 0
        cc = "up" if chg > 0 else "dn"
        rsi_c = "bad" if (it["rsi14"] or 0) > 70 else (
            "good" if (it["rsi14"] or 100) < 30 else "")
        tags = "".join(f"<span class='tag'>{n}</span>" for n in it["notes"])
        score_c = "up" if it["score"] > 0 else ("dn" if it["score"] < 0 else "")
        h.append(
            f"<tr><td><b>{s}</b></td><td class='mut'>{universe.group_of(s)}</td>"
            f"<td data-v='{sn['px']}'>{sn['px']:.2f}</td>"
            f"<td data-v='{chg:.2f}' class='{cc}'>{chg:+.2f}</td>"
            f"<td data-v='{it['adx'] or 0}'>{it['adx']:.0f}</td>"
            f"<td data-v='{it['rsi14'] or 0}'><span class='{rsi_c}'>"
            f"{it['rsi14'] or 0:.1f}</span></td>"
            f"<td data-v='{it['z20'] or 0}'>{it['z20'] or 0:+.2f}</td>"
            f"<td data-v='{it['atr_pct'] or 0}'>{it['atr_pct'] or 0:.2f}</td>"
            f"<td data-v='{it['rv20'] or 0}'>{it['rv20'] or 0:.1f}</td>"
            f"<td data-v='{it['pct252'] or 0}'>{it['pct252'] or 0:.0f}</td>"
            f"<td data-v='{it['vol_ratio'] or 0}'>{it['vol_ratio'] or 0:.2f}</td>"
            f"<td data-v='{it['score']}' class='{score_c}'>{it['score']:+d}</td>"
            f"<td>{tags}</td></tr>")
    h.append("</tbody></table>")

    # ===== GEX =====
    if gex_rows:
        h.append("<h2>二、GEX 结构（SPY，来自本地DB）</h2>")
        h.append("<table><thead><tr>"
                 "<th>到期</th><th>DTE</th><th>总GEX(M)</th><th>Flip</th>"
                 "<th>Call墙</th><th>Put墙</th><th>最大负gamma</th><th>体制</th>"
                 "</tr></thead><tbody>")
        for r in gex_rows:
            tot = r["total_gex"] or 0
            reg = ("<span class='tag bad'>负GEX</span>" if tot < 0
                   else "<span class='tag good'>正GEX</span>")
            h.append(f"<tr><td>{r['exp']}</td><td>{r['dte']}</td>"
                     f"<td data-v='{tot}'>{tot:+,.0f}</td>"
                     f"<td>{r['flip'] or '—'}</td>"
                     f"<td>{r['call_wall'] or '—'}</td>"
                     f"<td>{r['put_wall'] or '—'}</td>"
                     f"<td>{r['maxneg_k'] or '—'} ({r['maxneg_gex'] or 0:+.0f}M)</td>"
                     f"<td>{reg}</td></tr>")
        h.append("</tbody></table>")

    # ===== VIX 曲线 =====
    if vix_curve:
        h.append("<h2>三、VIX 期货期限结构</h2>")
        h.append("<table><thead><tr><th>合约</th><th>价格</th>"
                 "<th>相对近月%</th><th>结构</th></tr></thead><tbody>")
        base = vix_curve[0]["px"] if vix_curve else 0
        for c in vix_curve:
            rel = (c["px"] / base - 1) * 100 if base else 0
            h.append(f"<tr><td>{c['name']}</td>"
                     f"<td data-v='{c['px']}'>{c['px']:.2f}</td>"
                     f"<td>{rel:+.2f}</td>"
                     f"<td>{'<span class=\"tag good\">contango</span>' if rel > 0 else '<span class=\"tag bad\">backwardation</span>'}</td></tr>")
        h.append("</tbody></table>")

    # ===== 关注列表 =====
    h.append("<h2>四、今日关注</h2>")
    watch = []
    for s in universe_list:
        it = inds.get(s)
        if not it:
            continue
        reasons = []
        if it["rsi14"] and it["rsi14"] > 70:
            reasons.append("RSI超买")
        if it["rsi14"] and it["rsi14"] < 30:
            reasons.append("RSI超卖")
        if it["z20"] and abs(it["z20"]) > 2:
            reasons.append(f"偏离Z={it['z20']:+.1f}")
        if it["vol_ratio"] and it["vol_ratio"] > 2:
            reasons.append("放量2x+")
        if it["pct252"] and it["pct252"] > 95:
            reasons.append("接近年内高点")
        if it["pct252"] and it["pct252"] < 5:
            reasons.append("接近年内低点")
        if reasons:
            watch.append((s, reasons))
    if watch:
        h.append("<table><thead><tr><th>标的</th><th>分组</th><th>触发原因</th>"
                 "</tr></thead><tbody>")
        for s, rs in watch:
            tag = "".join(f"<span class='tag warn'>{r}</span>" for r in rs)
            h.append(f"<tr><td><b>{s}</b></td>"
                     f"<td class='mut'>{universe.group_of(s)}</td>"
                     f"<td>{tag}</td></tr>")
        h.append("</tbody></table>")
    else:
        h.append("<div class='note'>今天没有标的触发明确信号。</div>")

    h.append(f"<div class='note' style='margin-top:32px'>"
             f"数据源：富途（实时/延时）· CBOE（延迟15分）· 本地DB<br>"
             f"本报告仅用于研究，不构成投资建议。</div>")
    h.append(f"<script>{SORT_JS}</script></body></html>")
    return "\n".join(h)


def main():
    no_fetch = "--no-fetch" in sys.argv
    syms = universe.all_symbols()
    p(f"标的池 {len(syms)} 个: {', '.join(syms)}\n")

    # 1. 快照
    p("拉取快照 ...")
    try:
        snaps = fetch_snapshots(syms)
        p(f"  ✅ {len(snaps)}/{len(syms)}")
    except Exception as e:
        p(f"  ❌ 快照失败: {type(e).__name__}: {str(e)[:60]}")
        snaps = {}

    # 2. 日线 → 指标
    p("\n计算指标 ...")
    inds = {}
    for s in syms:
        df = load_bars(s)
        if (df is None or len(df) < 60) and not no_fetch:
            p(f"  {s} 补充历史 ...")
            fetch_bars(s)
            df = load_bars(s)
        if df is None or len(df) < 60:
            p(f"  {s} 数据不足，跳过")
            continue
        try:
            inds[s] = calc.latest_snapshot(df)
            p(f"  {s:6s} RSI {inds[s]['rsi14'] or 0:5.1f}  "
              f"ADX {inds[s]['adx'] or 0:4.0f}  "
              f"评分 {inds[s]['score']:+d}")
        except Exception as e:
            p(f"  {s} 计算失败: {type(e).__name__}")

    # 3. GEX（DB）
    gex_rows = []
    try:
        with db.connect() as conn:
            gex_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM gex_daily WHERE symbol='SPY' "
                "ORDER BY date DESC, dte LIMIT 8").fetchall()]
    except Exception:
        pass

    # 4. VIX 曲线
    vix_curve = []
    try:
        from sources import futu as fs
        rows = fs.snapshot(["US.VX2611", "US.VX2612", "US.VX2701",
                            "US.VX2702", "US.VX2703", "US.VX2706"])
        for r in rows:
            code = r["code"].replace("US.", "")
            vix_curve.append(dict(name=code, px=float(r["last_price"])))
    except Exception as e:
        p(f"  VIX曲线失败: {type(e).__name__}")

    # 5. 生成
    html = build(syms, snaps, inds, gex_rows, vix_curve)
    out = os.path.join(config.REPORT_DIR, f"dashboard_{datetime.now():%Y%m%d}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    # 同时写一份 latest，方便书签
    latest = os.path.join(config.REPORT_DIR, "dashboard_latest.html")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)
    p(f"\n✅ 看板已生成：{out}")
    p(f"   {latest}（固定链接，每次覆盖）")

    with open(os.path.join(config.DATA_DIR, "dashboard_run.log"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG))


if __name__ == "__main__":
    main()
