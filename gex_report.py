#!/usr/bin/env python3
"""
gex_report.py — 生成本地 HTML 图表报告（自包含，图片内嵌 base64）

读取 data/gex_multi.csv，为每个标的一张图：
  - ≥3 天：时间线（收盘价 + call/put wall + GEX 柱）
  - <3 天：单日概览（GEX 柱 + 墙位标注）

输出: reports/gex_report_YYYYMMDD.html（双击即可用浏览器打开）
"""
from __future__ import annotations

import base64
import csv
import io
import math
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.environ.get("GEX_CSV", os.path.join(HERE, "data", "gex_multi.csv"))
FONT_CANDIDATES = [
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/msyhbd.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
]


def font(size, bold=False):
    order = FONT_CANDIDATES[1::2] + FONT_CANDIDATES[0::2] if bold else FONT_CANDIDATES
    for p in order:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def load():
    if not os.path.exists(CSV):
        raise SystemExit(f"没有 {CSV}，先跑 gex_multi.py")
    by = {}
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["spot"] = float(r["spot"])
                r["gex"] = float(r["gex_musd"])
            except (TypeError, ValueError):
                continue
            for k in ("flip", "call_wall", "put_wall"):
                v = r.get(k)
                r[k] = float(v) if v not in (None, "", "None") else float("nan")
            by.setdefault(r["symbol"], []).append(r)
    for s in by:
        by[s].sort(key=lambda x: x["date"])
    return by


# ---------------------------------------------------------------- 画图
W, H = 1080, 380
M = dict(l=70, r=24, t=44, b=34)


def draw_timeline(rows, title):
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    f_t, f_s = font(15, True), font(10)
    n = len(rows)
    GAP = 26
    top_h = int((H - M["t"] - M["b"] - GAP) * 0.66)
    bot_h = H - M["t"] - M["b"] - GAP - top_h
    PW = W - M["l"] - M["r"]

    def px(i):
        return M["l"] + (i / max(n - 1, 1)) * PW

    prices = [r["spot"] for r in rows]
    walls = [v for r in rows for v in (r["call_wall"], r["put_wall"]) if math.isfinite(v)]
    lo = min(prices + walls)
    hi = max(prices + walls)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = lo - pad, hi + pad

    def py(p):
        return M["t"] + top_h - (p - lo) / (hi - lo) * top_h

    dr.text((M["l"], 12), title, font=f_t, fill=(20, 20, 20))
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        dr.line([(M["l"], py(v)), (M["l"] + PW, py(v))], fill=(240, 240, 240))
        dr.text((M["l"] - 6, py(v) - 5), f"{v:.0f}", font=f_s, fill=(140, 140, 140), anchor="ra")

    for key, col in (("call_wall", (214, 48, 48)), ("put_wall", (32, 108, 200))):
        pts = [(px(i), py(r[key])) for i, r in enumerate(rows) if math.isfinite(r[key])]
        for a, b in zip(pts, pts[1:]):
            dr.line([a, b], fill=col, width=2)
        for p in pts:
            dr.ellipse([p[0] - 2.5, p[1] - 2.5, p[0] + 2.5, p[1] + 2.5], fill=col)
    fp = [(px(i), py(r["flip"])) for i, r in enumerate(rows) if math.isfinite(r["flip"])]
    for a, b in zip(fp, fp[1:]):
        dr.line([a, b], fill=(160, 160, 160), width=1)
    dr.line([(px(i), py(r["spot"])) for i, r in enumerate(rows)], fill=(25, 25, 25), width=2)

    base = M["t"] + top_h + GAP
    gmax = max(abs(r["gex"]) for r in rows) or 1
    zero = base + bot_h / 2

    def gy(v):
        return zero - (v / gmax) * (bot_h / 2 - 4)

    dr.line([(M["l"], zero), (M["l"] + PW, zero)], fill=(160, 160, 160))
    bw = max(2.0, PW / n * 0.6)
    for i, r in enumerate(rows):
        y0, y1 = gy(0), gy(r["gex"])
        col = (20, 140, 80) if r["gex"] >= 0 else (200, 50, 50)
        dr.rectangle([px(i) - bw / 2, min(y0, y1), px(i) + bw / 2, max(y0, y1)], fill=col)
    dr.text((M["l"] - 6, gy(gmax) - 5), f"+{gmax:.0f}M", font=f_s, fill=(140, 140, 140), anchor="ra")
    dr.text((M["l"] - 6, zero - 5), "0", font=f_s, fill=(140, 140, 140), anchor="ra")

    step = max(1, n // 8)
    for i in range(0, n, step):
        dr.text((px(i), H - M["b"] + 6), rows[i]["date"][5:], font=f_s,
                fill=(130, 130, 130), anchor="ma")

    # 图例
    lx, ly = M["l"] + PW - 150, M["t"] + 2
    for lab, col in (("收盘价", (25, 25, 25)), ("call wall", (214, 48, 48)),
                     ("put wall", (32, 108, 200)), ("gamma flip", (160, 160, 160))):
        dr.rectangle([lx, ly + 2, lx + 10, ly + 8], fill=col)
        dr.text((lx + 14, ly - 2), lab, font=f_s, fill=(80, 80, 80))
        ly += 14
    return img


def draw_day(rows, title):
    """只有 1-2 天时：用横向卡片展示当日关键数字"""
    img = Image.new("RGB", (W, 220), "white")
    dr = ImageDraw.Draw(img)
    f_t, f_n, f_s = font(15, True), font(26, True), font(11)
    dr.text((M["l"], 12), title, font=f_t, fill=(20, 20, 20))
    r = rows[-1]
    items = [
        ("收盘价", f"{r['spot']:.2f}", (25, 25, 25)),
        ("总 GEX", f"{r['gex']:+.0f}M", (20, 140, 80) if r["gex"] >= 0 else (200, 50, 50)),
        ("call wall", f"{r['call_wall']:.2f}" if math.isfinite(r["call_wall"]) else "—", (214, 48, 48)),
        ("put wall", f"{r['put_wall']:.2f}" if math.isfinite(r["put_wall"]) else "—", (32, 108, 200)),
        ("gamma flip", f"{r['flip']:.2f}" if math.isfinite(r["flip"]) else "无", (150, 150, 150)),
        ("ATM IV", f"{float(r['atm_iv']) * 100:.0f}%", (80, 80, 80)),
    ]
    x = M["l"]
    for lab, val, col in items:
        dr.text((x, 62), lab, font=f_s, fill=(130, 130, 130))
        dr.text((x, 84), val, font=f_n, fill=col)
        x += (W - M["l"] - M["r"]) / len(items)
    dr.text((M["l"], 160), f"{r['date']}  还剩 {r['dte']} 天到期  "
                           f"1σ 预期 {r['sigma_1d_pct']}%  P/C {r['pcr_oi']}  "
                           f"样本 {r['strikes_used']} 档",
            font=f_s, fill=(120, 120, 120))
    dr.text((M["l"], 180), "累计 ≥3 个交易日后自动切换为时间线图", font=f_s, fill=(170, 170, 170))
    return img


def b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- HTML
def main():
    by = load()
    today = datetime.now().strftime("%Y%m%d")
    out = os.path.join(HERE, "reports", f"gex_report_{today}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    syms = sorted(by)
    latest_date = max(r["date"] for s in syms for r in by[s])

    # 汇总表
    hdr = ["标的", "日期", "收盘", "总 GEX", "call wall", "put wall",
           "gamma flip", "ATM IV", "1σ", "P/C", "天数"]
    trs = []
    for s in syms:
        r = by[s][-1]

        def fv(v, fmt="{:.2f}"):
            return fmt.format(v) if math.isfinite(v) else "—"
        gcl = "pos" if r["gex"] >= 0 else "neg"
        trs.append(
            f"<tr><td><b>{s}</b></td><td>{r['date']}</td><td>{r['spot']:.2f}</td>"
            f"<td class='{gcl}'>{r['gex']:+.0f}M</td>"
            f"<td>{fv(r['call_wall'])}</td><td>{fv(r['put_wall'])}</td>"
            f"<td>{fv(r['flip'])}</td><td>{float(r['atm_iv']) * 100:.0f}%</td>"
            f"<td>{r['sigma_1d_pct']}%</td><td>{r['pcr_oi']}</td>"
            f"<td>{len(by[s])}</td></tr>")

    cards = []
    for s in syms:
        rows = by[s]
        title = f"{s}  {'时间线' if len(rows) >= 3 else '当日概览'}"
        img = draw_timeline(rows, title) if len(rows) >= 3 else draw_day(rows, title)
        os.makedirs(os.path.join(HERE, 'reports', 'img'), exist_ok=True)
        img.save(os.path.join(HERE, 'reports', 'img', f'{s}.png'))
        cards.append(f"<div class='card'><img src='data:image/png;base64,{b64(img)}' alt='{s}'></div>")

    html = f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<title>GEX 报告 {latest_date}</title>
<style>
body{{margin:0;padding:28px;background:#fff;color:#1a1a1a;
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
h1{{font-size:21px;margin:0 0 4px}}
.sub{{color:#6b7280;font-size:12px;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums;margin-bottom:24px}}
th,td{{padding:6px 10px;text-align:right;border-bottom:1px solid #eee}}
th:first-child,td:first-child{{text-align:left}}
th{{color:#6b7280;font-weight:600;font-size:12px}}
.pos{{color:#0f766e;font-weight:600}}
.neg{{color:#b91c1c;font-weight:600}}
.card{{border:1px solid #eee;border-radius:10px;padding:10px;margin-bottom:16px}}
.card img{{width:100%;display:block}}
.note{{color:#9ca3af;font-size:12px;margin-top:20px;border-top:1px solid #eee;padding-top:12px}}
</style><body>
<h1>GEX / 期权墙位报告</h1>
<div class="sub">数据日期 {latest_date} ｜ 来源 CBOE 延迟链 ｜ 生成于 {datetime.now():%Y-%m-%d %H:%M}</div>
<table><thead><tr>{''.join(f'<th>{h}</th>' for h in hdr)}</tr></thead>
<tbody>{''.join(trs)}</tbody></table>
{''.join(cards)}
<div class="note">
GEX = Σ ±gamma×OI×100×S²×0.01（call 为正、put 为负）。<br>
墙位 = 现价 ±4% 内 |GEX| 最大的行权价。<br>
累计 ≥3 个交易日后，每张图自动切换为「收盘价 + 墙位 + GEX」时间线。
</div></body></html>"""

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告 -> {out}  ({len(syms)} 个标的, 最新 {latest_date})")


if __name__ == "__main__":
    main()



