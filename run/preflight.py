#!/usr/bin/env python3
"""
run/preflight.py — 开盘前体检（1 分钟跑完）

检查清单：
  ① IB Gateway 端口是否开放
  ② 能否连接
  ③ 行情模式（实时 vs 延迟）← 最关键
  ④ 期权链能否取到（含 greeks）
  ⑤ 价差是否可交易
  ⑥ 当前市场时段

用法：
    python run/preflight.py
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import config  # noqa: E402
config.setup_path()


def section(title):
    print()
    print("=" * 68)
    print(f" {title}")
    print("=" * 68)


def market_session():
    """当前美股时段（美东）"""
    now_utc = datetime.now(timezone.utc)
    # 美东：EST = UTC-5, EDT = UTC-4。3月第二个周日到11月第一个周日是 EDT
    # 简化：11月-2月用 EST，3-10月用 EDT
    m = now_utc.month
    offset = -5 if (m in (11, 12, 1, 2)) else -4
    et = now_utc + timedelta(hours=offset)
    wd = et.weekday()
    hhmm = et.hour * 100 + et.minute

    if wd >= 5:
        return et, "周末休市"
    if 400 <= hhmm < 930:
        return et, "盘前"
    if 930 <= hhmm < 1600:
        return et, "盘中（RTH）✅ 最佳时段"
    if 1600 <= hhmm < 2000:
        return et, "盘后"
    return et, "夜盘"


def main():
    print("IBKR 开盘前体检")
    print(f"本地时间 {datetime.now():%Y-%m-%d %H:%M:%S}")

    et, sess = market_session()
    print(f"美东时间 {et:%Y-%m-%d %H:%M:%S}  →  {sess}")

    section("① 端口检查")
    import socket
    port = config.IBKR_PORT
    ok_port = False
    for p in (config.IBKR_PORT, 4002, 7496, 7497):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            r = s.connect_ex(("127.0.0.1", p))
            status = "开放 ✅" if r == 0 else "关闭"
            if r == 0:
                ok_port = True
                port = p
        except Exception:
            status = "错误"
        finally:
            s.close()
        print(f"   端口 {p}: {status}")

    if not ok_port:
        print("\n❌ 没有开放的端口")
        print("   → 请打开 IB Gateway 并登录")
        print("   → 确认 API 设置里勾选了「Enable ActiveX and Socket Clients」")
        return 1

    config.IBKR_PORT = port

    section("② 连接测试")
    try:
        from sources.ibkr import IBKRSource
    except ImportError:
        print("   ❌ ib_async 未安装")
        return 1

    src = IBKRSource(port=port)
    try:
        src.connect(timeout=15)
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {str(e)[:60]}")
        return 1
    print(f"   ✅ 已连接")
    print(f"   行情模式: {src.mode}  "
          f"({'✅ 实时' if src.mode == 'realtime' else '⚠️ 延迟（需要 OPRA 订阅）'})")

    section("③ 美股期权数据测试")
    sym = "SOXS"
    try:
        spot, _ = src.spot(sym)
        print(f"   {sym} 现价: {spot}")
        if spot is None:
            print("   ❌ 无现价")
            return 1

        exps = src.expiries(sym)
        print(f"   到期日: {exps[:5]}")
        if not exps:
            print("   ❌ 无到期日")
            return 1

        exp = exps[0]
        strikes = src.strikes(sym, exp)
        print(f"   行权价数量: {len(strikes)}")

        chain = src.option_chain(sym, exp, spot=spot, n_strikes=10)
        print(f"   期权合约返回: {len(chain)} 个")

        if not chain:
            print("   ❌ 无期权数据（可能没有 OPRA 订阅）")
            return 1

        with_greeks = [c for c in chain if c.get("iv")]
        with_bid = [c for c in chain if c.get("bid") and c["bid"] > 0]
        print(f"   有 IV 的: {len(with_greeks)}/{len(chain)}")
        print(f"   有买价的: {len(with_bid)}/{len(chain)}")

        if not with_bid:
            print("   ❌ 全部没有买价 → 大概率缺 OPRA 订阅")
            print("   在 IBKR 客户端查看：Account → Market Data Subscriptions")
            return 1

        section("④ 价差体检")
        near = [c for c in with_bid if abs(c["strike"] / spot - 1) < 0.1
                and c["spread_pct"]]
        if near:
            sp = sorted(c["spread_pct"] for c in near)
            med = sp[len(sp) // 2]
            print(f"   平值附近中位价差: {med:.1f}%")
            if med < 5:
                print("   ✅ 可交易（<5%）")
            elif med < 15:
                print("   🟡 勉强（5-15%）")
            else:
                print("   🔴 太宽（>15%），等盘中有流动性再试")

        section("⑤ 可执行组合预览")
        calls = [c for c in with_bid if c["right"] == "C"]
        if calls:
            for c in sorted(calls, key=lambda x: x["strike"])[:6]:
                print(f"   {c['strike']:>7.1f}C  bid {c['bid']:.2f}  "
                      f"ask {c['ask'] or 0:.2f}  价差 {c['spread'] or 0:.2f}  "
                      f"IV {(c['iv'] or 0)*100:.0f}%")

    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {str(e)[:80]}")
        return 1
    finally:
        try:
            src.ib.disconnect()
        except Exception:
            pass

    section("结论")
    print("   ✅ 全部通过，可以盘中跑：")
    print("      python run/intraday_monitor.py")
    print("      或双击 intraday.bat")
    if sess and "盘中" not in sess:
        print(f"\n   ⚠️ 当前是「{sess}」，盘中再跑才有意义")
        print("      美股盘中 = 北京 22:30–05:00（冬令时）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
