"""
core/charts.py — 纯 Python 生成内联 SVG 图表

优点：不依赖任何 JS 库，HTML 完全自包含，GitHub Pages / 邮件都能直接看。

提供：
  line_svg()     折线图（价格走势、VIX 期限）
  bar_svg()      柱状图（GEX 按到期）
  sparkline()    迷你走势线（表格里用）
"""


def _scale(vals, lo, hi, height, top=0):
    """数值 → y 坐标（y 轴向下）"""
    if hi == lo:
        return [top + height / 2] * len(vals)
    return [top + height - (v - lo) / (hi - lo) * height for v in vals]


def line_svg(series, width=520, height=140, pad=36,
             colors=None, labels=None, fmt="{:.2f}",
             show_axes=True, fill=False):
    """折线图

    series: list[dict(data=[...], name=str, color=str)]
    labels: X 轴标签（与 data 等长）
    """
    n = max(len(s["data"]) for s in series) if series else 0
    if n < 2:
        return "<div style='color:#999;font-size:12px'>数据不足</div>"

    allv = [v for s in series for v in s["data"] if v is not None]
    if not allv:
        return ""
    lo, hi = min(allv), max(allv)
    pad_v = (hi - lo) * 0.08 or abs(hi) * 0.02 or 1
    lo, hi = lo - pad_v, hi + pad_v

    W, H = width, height
    plot_w = W - pad * 2
    plot_h = H - pad

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'style="max-width:{W}px;display:block">']

    # 网格 + Y 轴刻度
    if show_axes:
        for i in range(4):
            y = pad - pad / 2 + (plot_h * i / 3)
            v = hi - (hi - lo) * i / 3
            parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{W-pad}" '
                         f'y2="{y:.1f}" stroke="#eee" stroke-width="1"/>')
            parts.append(f'<text x="{pad-4}" y="{y+4:.1f}" text-anchor="end" '
                         f'font-size="10" fill="#999">{fmt.format(v)}</text>')

    # 折线
    for si, s in enumerate(series):
        data = s["data"]
        m = len(data)
        if m < 2:
            continue
        xs = [pad + plot_w * i / (m - 1) for i in range(m)]
        ys = _scale(data, lo, hi, plot_h, pad - pad / 2)
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
        color = s.get("color") or (colors[si % len(colors)] if colors else "#0a58ca")
        if fill:
            area = f"{pad},{pad-pad/2+plot_h:.1f} " + pts + \
                   f" {pad+plot_w:.1f},{pad-pad/2+plot_h:.1f}"
            parts.append(f'<polygon points="{area}" fill="{color}" '
                         f'opacity="0.10"/>')
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="1.8" stroke-linejoin="round"/>')
        # 末点
        parts.append(f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3" '
                     f'fill="{color}"/>')
        # 名称
        if s.get("name") and len(series) > 1:
            parts.append(f'<text x="{pad+2}" y="{pad-6+si*13}" font-size="10" '
                         f'fill="{color}">{s["name"]}</text>')

    # X 轴标签
    if labels and len(labels) >= 2:
        for i in (0, len(labels) // 2, len(labels) - 1):
            x = pad + plot_w * i / (len(labels) - 1)
            parts.append(f'<text x="{x:.1f}" y="{H-6}" text-anchor="middle" '
                         f'font-size="10" fill="#999">{labels[i]}</text>')

    parts.append("</svg>")
    return "".join(parts)


def bar_svg(items, width=520, height=150, pad=40,
            pos_color="#1e8e3e", neg_color="#d93025", fmt="{:.0f}"):
    """柱状图（正负分色）

    items: list[dict(label=str, value=float)]
    """
    if not items:
        return ""
    vals = [i["value"] for i in items]
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    if hi == lo:
        hi = lo + 1
    W, H = width, height
    plot_w = W - pad * 2
    plot_h = H - pad
    n = len(items)
    bw = plot_w / n * 0.62

    zero_y = pad - pad / 2 + plot_h - (0 - lo) / (hi - lo) * plot_h

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'style="max-width:{W}px;display:block">']
    parts.append(f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{W-pad}" '
                 f'y2="{zero_y:.1f}" stroke="#ccc" stroke-width="1"/>')

    for i, it in enumerate(items):
        v = it["value"]
        cx = pad + plot_w * (i + 0.5) / n
        y = pad - pad / 2 + plot_h - (v - lo) / (hi - lo) * plot_h
        top = min(y, zero_y)
        bh = abs(zero_y - y)
        color = pos_color if v >= 0 else neg_color
        parts.append(f'<rect x="{cx-bw/2:.1f}" y="{top:.1f}" '
                     f'width="{bw:.1f}" height="{max(bh,1):.1f}" '
                     f'fill="{color}" opacity="0.75" rx="2"/>')
        parts.append(f'<text x="{cx:.1f}" y="{H-16}" text-anchor="middle" '
                     f'font-size="10" fill="#666">{it["label"]}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{H-4}" text-anchor="middle" '
                     f'font-size="9" fill="#999">{fmt.format(v)}</text>')

    parts.append("</svg>")
    return "".join(parts)


def sparkline(vals, width=90, height=22, color="#0a58ca"):
    """迷你走势线"""
    if not vals or len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    n = len(vals)
    pts = " ".join(
        f"{width*i/(n-1):.1f},{height-2-(v-lo)/(hi-lo)*(height-4):.1f}"
        for i, v in enumerate(vals))
    return (f'<svg width="{width}" height="{height}" '
            f'style="vertical-align:middle">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.4"/></svg>')


if __name__ == "__main__":
    # 自测：生成一个示例 HTML
    import math
    xs = [math.sin(i / 5) * 10 + i * 0.2 for i in range(60)]
    labels = [f"d{i}" for i in range(60)]
    svg1 = line_svg([dict(data=xs, name="示例", color="#0a58ca")],
                    labels=labels, fill=True)
    svg2 = bar_svg([dict(label=f"E{i}", value=(i - 3) * 120) for i in range(7)])
    svg3 = sparkline(xs[:20])

    html = (f"<html><body style='font-family:sans-serif'>"
            f"<h3>折线</h3>{svg1}<h3>柱状</h3>{svg2}"
            f"<h3>迷你线</h3>{svg3}</body></html>")
    with open("charts_test.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成 charts_test.html")
