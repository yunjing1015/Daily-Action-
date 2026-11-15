#!/usr/bin/env python3
"""
run/intraday.py — 盘中实时分析（带可执行价）

核心问题：收盘时买卖价差是盘中的 18–30 倍，策略效果完全不同。
        盘中 QQQ 期权价差 0.01，收盘 0.18。

数据源优先级：
  ① IBKR（实时期权链，需要 Gateway + OPRA 权限）
  ② CBOE（15 分钟延迟，免费，慢但稳）

输出：
  ① 各标的实时价差体检（能不能交易）
  ② GEX 结构（用真实 IV）
  ③ 可执行的价差组合（真实 bid/ask，含成本）
  ④ 熊市看涨价差实时信号（用真实价格）

用法：
    python run/intraday.py                    # 默认核心 4 标的
    python run/intraday.py SPY QQQ SOXL       # 指定标的
    python run/intraday.py --source cboe      # 强制用 CBOE
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

LOG = []


def p(s=""):
    LOG.append(str(s))
    print(s, flush=True)


# ---------------- 数据源 ----------------
def try_ibkr(symbols):
    """尝试 IBKR，返回 {sym: {spot, chain}} 或 None"""
    try:
        from sources.ibkr import IBKRSource
    except ImportError:
        return None
    try:
        src = IBKRSource()
        src.connect(timeout=10)
    except Exception as e:
        p(f"  IBKR 连接失败: {type(e).__name__}")
        return None
    if not src.is_connected():
        p("  IBKR 未连接")
        return None

    out = {}
    try:
        for sym in symbols:
            try:
                spot, _ = src.spot(sym)
                if spot is None:
                    p(f"  {sym}: 无现货")
                    continue
                exps = src.expiries(sym)
                if not exps:
                    p(f"  {sym}: 无到期日")
                    continue
                exp = exps[0]
                chain = src.option_chain(sym, exp, spot=spot)
                if chain:
                    out[sym] = dict(spot=spot, exp=exp, chain=chain,
                                    source="ibkr")
                    p(f"  {sym}: 现货 {spot:.2f}  到期 {exp}  "
                      f"{len(chain)} 合约")
            except Exception as e:
                p(f"  {sym} 失败: {type(e).__name__}: {str(e)[:40]}")
    finally:
        try:
            src.ib.disconnect()
        except Exception:
            pass
    return out or None


def try_cboe(symbols):
    """CBOE 延迟数据（兜底）"""
    from sources import cboe
    out = {}
    for sym in symbols:
        try:
            raw = cboe.fetch_raw(sym, tries=3, timeout=90)
            cur, close, *_ = cboe.underlying(raw)
            chain = cboe.parse_chain(sym, raw, max_dte=45)
            if not chain:
                continue
            S = cboe.implied_spot(chain, close or cur)
            exp = min(c["exp"] for c in chain)
            sub = [c for c in chain if c["exp"] == exp]
            # 统一字段名（和 IBKR 对齐）
            for c in sub:
                b, a, m = c.get("bid"), c.get("ask"), c.get("mid")
                c["spread"] = (a - b) if (b and a and b > 0) else None
                c["spread_pct"] = ((a - b) / m * 100
                                   if (b and a and m and b > 0) else None)
                c["liquid"] = bool(b and a and b > 0 and m and
                                   (a - b) / m < 0.15)
            out[sym] = dict(spot=S, exp=exp, chain=sub, source="cboe_delayed")
            p(f"  {sym}: 隐含现货 {S:.2f}  到期 {exp}  {len(sub)} 合约")
        except Exception as e:
            p(f"  {sym} 失败: {type(e).__name__}")
    return out or None


# ---------------- 分析 ----------------
def spread_health(data):
    """价差体检：这个标的现在能不能交易"""
    rows = []
    for sym, d in data.items():
        chain = d["chain"]
        S = d["spot"]
        # 只看平值附近（±5%）的合约
        near = [c for c in chain if abs(c["strike"] / S - 1) < 0.05
                and c.get("mid") and c["mid"] > 0.05]
        if not near:
            continue
        spreads = [c["spread_pct"] for c in near if c.get("spread_pct")]
        if not spreads:
            continue
        spreads.sort()
        med = spreads[len(spreads) // 2]
        n_liquid = sum(1 for c in near if c.get("liquid"))
        rows.append(dict(symbol=sym, exp=d["exp"], n=len(near),
                         med_spread=med,
                         p90=spreads[int(len(spreads) * 0.9)],
                         liquid=n_liquid,
                         liquid_pct=n_liquid / len(near) * 100,
                         source=d["source"]))
    rows.sort(key=lambda r: r["med_spread"])
    return rows


def gex_snapshot(data):
    """用真实 IV 算 GEX"""
    out = []
    for sym, d in data.items():
        chain = d["chain"]
        S = d["spot"]
        gk = {}
        oc, op = {}, {}
        for c in chain:
            if not c.get("gamma") or not c.get("oi"):
                continue
            v = c["gamma"] * c["oi"] * 100 * S * S * 0.01
            if c["right"] == "C":
                gk[c["strike"]] = gk.get(c["strike"], 0) + v
                oc[c["strike"]] = oc.get(c["strike"], 0) + c["oi"]
            else:
                gk[c["strike"]] = gk.get(c["strike"], 0) - v
                op[c["strike"]] = op.get(c["strike"], 0) + c["oi"]
        if not gk:
            continue
        total = sum(gk.values())
        ks = sorted(gk)
        band = [k for k in ks if S * 0.96 <= k <= S * 1.04]
        cw = max((k for k in band if k >= S), key=lambda k: gk[k], default=None)
        pw = max((k for k in band if k < S), key=lambda k: gk[k], default=None)
        mnk = min(ks, key=lambda k: gk[k])
        out.append(dict(symbol=sym, spot=S, total_gex=total,
                        call_wall=cw, put_wall=pw,
                        maxneg_k=mnk, maxneg_gex=gk[mnk],
                        n=len(chain), source=d["source"]))
    out.sort(key=lambda r: r["total_gex"])
    return out


def executable_spreads(data, sell_otm=0.15, buy_otm=0.30, n_lots=10):
    """用真实 bid/ask 算可执行的熊市看涨价差"""
    out = []
    for sym, d in data.items():
        chain = d["chain"]
        S = d["spot"]
        calls = {c["strike"]: c for c in chain
                 if c["right"] == "C" and c.get("bid") and c.get("ask")
                 and c["bid"] > 0}
        if not calls:
            continue
        K_sell = min(calls, key=lambda k: abs(k - S * (1 + sell_otm)))
        K_buy = min(calls, key=lambda k: abs(k - S * (1 + buy_otm)))
        if K_sell >= K_buy:
            continue
        cs, cb = calls[K_sell], calls[K_buy]
        # 真实成交：卖收 bid，买付 ask
        credit = (cs["bid"] - cb["ask"]) * 100 * n_lots - 4.0 * n_lots
        width = (K_buy - K_sell) * 100 * n_lots
        max_loss = width - credit
        if max_loss <= 0:
            continue
        out.append(dict(symbol=sym, spot=S, exp=d["exp"],
                        k_sell=K_sell, k_buy=K_buy,
                        sell_bid=cs["bid"], sell_ask=cs["ask"],
                        buy_bid=cb["bid"], buy_ask=cb["ask"],
                        spread_sell=cs["spread"], spread_buy=cb["spread"],
                        iv_sell=(cs["iv"] * 100 if cs.get("iv") else None),
                        iv_buy=(cb["iv"] * 100 if cb.get("iv") else None),
                        credit=credit, max_loss=max_loss,
                        ror=credit / max_loss * 100 if max_loss > 0 else 0,
                        n=n_lots, source=d["source"]))
    out.sort(key=lambda r: -r["ror"])
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = None
    for a in sys.argv[1:]:
        if a.startswith("--source"):
            force = a.split("=")[-1] if "=" in a else "cboe"
    syms = args or ["SOXS", "SQQQ", "UVXY", "VXX"]

    p(f"盘中实时分析 | {datetime.now():%Y-%m-%d %H:%M}")
    p(f"标的: {', '.join(syms)}\n")

    data = None
    if force != "cboe":
        p("① 尝试 IBKR（实时期权）...")
        data = try_ibkr(syms)
    if not data:
        p("\n② 回退到 CBOE（15 分钟延迟）...")
        data = try_cboe(syms)
    if not data:
        p("\n❌ 无数据源可用")
        p("   IBKR: 需要打开 IB Gateway 并登录")
        p("   CBOE: 网络问题，稍后重试")
        return

    # ===== 价差体检 =====
    p("\n" + "=" * 90)
    p("① 价差体检（现在能不能交易）")
    p("=" * 90)
    health = spread_health(data)
    if health:
        p(f"{'标的':>7}{'到期':>12}{'合约':>6}{'中位价差%':>11}"
          f"{'P90价差%':>10}{'流动性':>10}{'源':>10}")
        for r in health:
            flag = "✅" if r["med_spread"] < 5 else ("🟡" if r["med_spread"] < 15 else "🔴")
            p(f"{r['symbol']:>7}{r['exp']:>12}{r['n']:>6}"
              f"{r['med_spread']:>10.1f}%{r['p90']:>9.1f}%"
              f"{r['liquid_pct']:>9.0f}%{r['source']:>10} {flag}")
        p("\n➜ 中位价差 <5% = 可交易；>15% = 不要碰")

    # ===== GEX =====
    p("\n" + "=" * 90)
    p("② GEX 结构（真实 IV）")
    p("=" * 90)
    gx = gex_snapshot(data)
    if gx:
        p(f"{'标的':>7}{'现货':>10}{'总GEX(M)':>12}{'Call墙':>9}"
          f"{'Put墙':>9}{'最大负gamma':>12}{'体制':>8}")
        for r in gx:
            reg = "负GEX" if r["total_gex"] < 0 else "正GEX"
            p(f"{r['symbol']:>7}{r['spot']:>10.2f}"
              f"{r['total_gex']/1e6:>+11.0f}"
              f"{r['call_wall'] or 0:>9.0f}{r['put_wall'] or 0:>9.0f}"
              f"{r['maxneg_k']:>8.0f}({r['maxneg_gex']/1e6:+.0f}M)"
              f"{reg:>8}")

    # ===== 可执行价差 =====
    p("\n" + "=" * 90)
    p("③ 可执行的熊市看涨价差（真实 bid/ask，含成本）")
    p("=" * 90)
    ex = executable_spreads(data)
    if ex:
        p(f"{'标的':>7}{'卖K':>8}{'买K':>8}{'卖bid':>8}{'买ask':>8}"
          f"{'价差C':>8}{'价差P':>8}{'收权利$':>10}{'最大亏$':>10}{'ROR%':>8}")
        for r in ex:
            p(f"{r['symbol']:>7}{r['k_sell']:>8.1f}{r['k_buy']:>8.1f}"
              f"{r['sell_bid']:>8.2f}{r['buy_ask']:>8.2f}"
              f"{r['spread_sell']:>8.2f}{r['spread_buy']:>8.2f}"
              f"{r['credit']:>+10,.0f}{r['max_loss']:>10,.0f}{r['ror']:>+7.1f}%")
        p(f"\n（{ex[0]['n']} 张为一组，手续费 $1/张/边）")
    else:
        p("无可用组合（权利金倒挂或无报价）")

    # ===== 结论 =====
    p("\n" + "=" * 90)
    p("④ 结论")
    p("=" * 90)
    if health and ex:
        tradeable = [r for r in health if r["med_spread"] < 5]
        if tradeable:
            p(f"✅ 现在可交易: {', '.join(r['symbol'] for r in tradeable)}")
        else:
            p("🔴 所有标的价差都超过 5%，不建议现在开仓")
        if ex:
            best = ex[0]
            p(f"✅ 最优组合: {best['symbol']} "
              f"卖 {best['k_sell']:.1f}C / 买 {best['k_buy']:.1f}C，"
              f"收 ${best['credit']:.0f}，风险 ${best['max_loss']:.0f} "
              f"({best['ror']:.1f}%)")

    # 保存
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = config.DATA_DIR
    if ex:
        path = os.path.join(out_dir, f"intraday_spreads_{stamp}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(ex[0].keys()))
            w.writeheader()
            w.writerows(ex)
        p(f"\nCSV: {path}")


if __name__ == "__main__":
    main()

