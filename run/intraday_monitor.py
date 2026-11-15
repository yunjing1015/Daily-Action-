#!/usr/bin/env python3
"""
run/intraday_monitor.py — 盘中实时监控（IBKR 实时行情）

设计：
  启动时：取现货 + 期权链（算 IV/GEX/OI）→ 确定要交易的合约
  循环中：每 N 秒只刷新「参与价差的那几个合约」+ 现货（快）
  输出：实时可执行价格、价差健康度、盈亏触发

监控目标（默认长期衰减的 ETF）：
  SOXS, SQQQ, UVXY, VXX, LABD, TZA

用法：
    python run/intraday_monitor.py                 # 默认监控
    python run/intraday_monitor.py SOXS SQQQ       # 指定
    python run/intraday_monitor.py --interval 15   # 15 秒刷新
"""
import math
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config, ivfilter  # noqa: E402
config.setup_path()

LOG_PATH = os.path.join(config.DATA_DIR, "intraday_monitor.log")


def log(s):
    line = f"[{datetime.now():%H:%M:%S}] {s}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def pick_expiry(exps, prefer_days=30):
    """选最接近 prefer_days 的到期日"""
    from datetime import datetime as dt
    today = dt.now().date()
    best, best_diff = None, 1e9
    for e in exps[:12]:
        try:
            d = (dt.strptime(e, "%Y%m%d").date() - today).days
        except ValueError:
            continue
        if d < 5:
            continue
        diff = abs(d - prefer_days)
        if diff < best_diff:
            best_diff, best = diff, e
    return best or (exps[0] if exps else None)


def nearest_strike(strikes, target):
    if not strikes:
        return None
    return min(strikes, key=lambda k: abs(k - target))


# 候选结构（卖/买 虚值距离）
CANDIDATES = [
    (0.08, 0.18), (0.10, 0.22), (0.12, 0.25),
    (0.15, 0.28), (0.15, 0.30), (0.18, 0.32),
    (0.20, 0.35), (0.22, 0.40),
]


def analyze_symbol(src, sym, n_lots=10, top_n=2):
    """搜索最优结构：遍历多个宽度，按 ROR 排序"""
    spot, st = src.spot(sym)
    if spot is None:
        return None
    exps = src.expiries(sym)
    if not exps:
        return None
    exp = pick_expiry(exps)
    strikes = src.strikes(sym, exp)
    if not strikes:
        return None

    results = []
    seen = set()
    for so, bo in CANDIDATES:
        K_sell = nearest_strike(strikes, spot * (1 + so))
        K_buy = nearest_strike(strikes, spot * (1 + bo))
        if K_sell is None or K_buy is None or K_buy <= K_sell:
            continue
        key = (K_sell, K_buy)
        if key in seen:
            continue
        seen.add(key)

        try:
            c_sell = src.make_option(sym, exp, K_sell, "C")
            c_buy = src.make_option(sym, exp, K_buy, "C")
            src.ib.qualifyContracts(c_sell, c_buy)
            q_sell = src.quote_option(c_sell)
            q_buy = src.quote_option(c_buy)
        except Exception:
            continue
        if not q_sell or not q_buy:
            continue
        if not (q_sell["bid"] and q_buy["ask"]):
            continue

        credit = (q_sell["bid"] - q_buy["ask"]) * 100 * n_lots
        fee = (n_lots + n_lots) * 1.0 * 2
        net_credit = credit - fee
        width = (K_buy - K_sell) * 100 * n_lots
        max_loss = width - net_credit
        if max_loss <= 0:
            continue

        results.append(dict(
            symbol=sym, exp=exp, spot=spot,
            k_sell=K_sell, k_buy=K_buy, n=n_lots,
            sell_bid=q_sell["bid"], sell_ask=q_sell["ask"],
            sell_spread=q_sell["spread"], sell_iv=q_sell["iv"],
            buy_bid=q_buy["bid"], buy_ask=q_buy["ask"],
            buy_spread=q_buy["spread"], buy_iv=q_buy["iv"],
            sell_otm=so, buy_otm=bo,
            credit=net_credit, max_loss=max_loss,
            ror=(net_credit / max_loss * 100),
            contracts=(c_sell, c_buy)))

    if not results:
        return None
    # 按 ROR 排序，返回最优
    results.sort(key=lambda r: -r["ror"])
    best = results[0]

    # 计算 IV 分位（用平值附近中位 IV 对比历史 RV 分布）
    try:
        from sources import ibkr as _ibk
        iv_med = ivfilter.iv_median_from_chain(
            [dict(strike=r["k_sell"], iv=r["sell_iv"] or 0) for r in results] +
            [dict(strike=r["k_buy"], iv=r["buy_iv"] or 0) for r in results],
            spot)
    except Exception:
        iv_med = None
    if iv_med is None:
        iv_med = max((r["sell_iv"] or 0) for r in results)
    best["iv_med"] = iv_med
    best["iv_pct"] = ivfilter.percentile(sym, iv_med)
    best["iv_grade"] = ivfilter.grade(best["iv_pct"])
    best["alternatives"] = results[1:top_n]
    best["n_candidates"] = len(results)
    return best


def refresh(src, item, n_lots=None):
    """只刷新两个合约 + 现货"""
    n = n_lots or item["n"]
    q_sell = src.quote_option(item["contracts"][0])
    q_buy = src.quote_option(item["contracts"][1])
    spot, _ = src.spot(item["symbol"], cache_s=1)
    if not q_sell or not q_buy or not q_sell["bid"] or not q_buy["ask"]:
        return None
    credit = (q_sell["bid"] - q_buy["ask"]) * 100 * n
    fee = 2 * n * 1.0 * 2
    net = credit - fee
    width = (item["k_buy"] - item["k_sell"]) * 100 * n
    ml = width - net
    return dict(sell_bid=q_sell["bid"], buy_ask=q_buy["ask"],
                sell_spread=q_sell["spread"], buy_spread=q_buy["spread"],
                credit=net, max_loss=ml,
                ror=(net / ml * 100) if ml > 0 else 0,
                spot=spot)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    interval = 10
    for a in sys.argv[1:]:
        if a.startswith("--interval"):
            try:
                interval = int(a.split("=")[-1])
            except ValueError:
                pass
    syms = args or ["SOXS", "SQQQ", "UVXY", "VXX"]

    log("=" * 78)
    log(f"盘中实时监控 | 标的: {', '.join(syms)} | 刷新 {interval}s")
    log("=" * 78)

    try:
        from sources.ibkr import IBKRSource
    except ImportError as e:
        log(f"❌ ib_async 未安装: {e}")
        return

    src = IBKRSource()
    try:
        src.connect()
    except Exception as e:
        log(f"❌ 连接失败: {type(e).__name__}: {str(e)[:60]}")
        log("   请确认 IB Gateway 已打开并登录（端口 4001）")
        return

    log(f"✅ 已连接  行情模式: {src.mode}")

    # ---- 初始化：算一次完整结构 ----
    items = {}
    log("\n初始化（首次较慢）...")
    for sym in syms:
        try:
            it = analyze_symbol(src, sym)
            if it:
                items[sym] = it
                ivp = it.get("iv_pct")
                ivg, ivm = it.get("iv_grade", ("未知", ""))
                if ivp is not None:
                    iv_s = f"IV分位 {ivp:>3.0f}% {ivm}{ivg}"
                else:
                    iv_s = "IV分位  —"
                if ivp is not None and ivp >= 60 and it["credit"] > 0:
                    tag = "✅可做"
                elif ivp is not None and ivp < 60:
                    tag = "⚠️IV低(不卖)"
                elif it["credit"] <= 0:
                    tag = "🔴倒挂"
                else:
                    tag = "❔未知"
                log(f"  {sym:6s} 现货 {it['spot']:>8.2f}  "
                    f"最优: 卖{it['k_sell']:.1f}C({it['sell_bid']:.2f}) "
                    f"买{it['k_buy']:.1f}C({it['buy_ask']:.2f})  "
                    f"收${it['credit']:>+7.0f} 风险${it['max_loss']:>8.0f} "
                    f"ROR {it['ror']:>+6.1f}%  {iv_s}  {tag}")
                for alt in it.get("alternatives", []):
                    log(f"         备选: 卖{alt['k_sell']:.1f}C "
                        f"买{alt['k_buy']:.1f}C  "
                        f"收${alt['credit']:>+7.0f} ROR {alt['ror']:>+6.1f}%")
            else:
                log(f"  {sym:6s} 无数据（可能无期权或无报价）")
        except Exception as e:
            log(f"  {sym:6s} 失败: {type(e).__name__}")

    if not items:
        log("\n❌ 所有标的都失败")
        src.ib.disconnect()
        return

    log(f"\n开始循环监控（Ctrl+C 停止）\n")

    # ---- 循环 ----
    count = 0
    try:
        while True:
            count += 1
            if not src.is_connected():
                log("⚠ 连接断开，重连中...")
                if not src.reconnect():
                    log("❌ 重连失败，退出")
                    break
                log("✅ 已重连")

            parts = []
            for sym, it in items.items():
                try:
                    r = refresh(src, it)
                    if not r:
                        parts.append(f"{sym} 无报价")
                        continue
                    ivp = it.get("iv_pct")
                    if ivp is not None and ivp < 60:
                        flag = "⚠️"     # IV 低，不该卖
                    elif r["credit"] > 0 and r["ror"] > 5:
                        flag = "🟢"     # 可做
                    elif r["credit"] > 0:
                        flag = "🟡"     # 鸡肋
                    else:
                        flag = "🔴"     # 倒挂
                    ivp_s = f"IV{ivp:.0f}% " if ivp is not None else ""
                    parts.append(
                        f"{sym} {r['spot']:.2f} "
                        f"卖{it['k_sell']:.0f}C@{r['sell_bid']:.2f} "
                        f"买{it['k_buy']:.0f}C@{r['buy_ask']:.2f} "
                        f"收${r['credit']:+.0f} ROR{r['ror']:+.0f}% "
                        f"{ivp_s}{flag}")
                except Exception as e:
                    parts.append(f"{sym} 异常")

            log(" | ".join(parts))

            # 每 30 轮汇总一次
            if count % 30 == 0:
                log("-" * 78)
                best = None
                for sym, it in items.items():
                    try:
                        r = refresh(src, it)
                        ivp = it.get("iv_pct")
                        if r and r["credit"] > 0 and ivp is not None and ivp >= 60:
                            if best is None or r["ror"] > best[1]:
                                best = (sym, r["ror"], r["credit"])
                    except Exception:
                        pass
                if best:
                    log(f"💡 当前最优（IV≥60%）: {best[0]}  ROR {best[1]:+.1f}%  "
                        f"收 ${best[2]:+.0f}")
                    log(f"   → 可执行：卖 {items[best[0]]['k_sell']:.1f}C / "
                        f"买 {items[best[0]]['k_buy']:.1f}C × "
                        f"{items[best[0]]['n']} 张")
                else:
                    log("💡 当前没有符合条件的组合（需 IV≥60% 且权利金为正）")
                log("-" * 78)

            time.sleep(interval)

    except KeyboardInterrupt:
        log("\n手动停止")
    finally:
        try:
            src.ib.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()






