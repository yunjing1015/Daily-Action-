"""
sources/ibkr.py — IBKR 期权数据源（实时/延迟自动切换）

关键经验：
  ① reqMktData 流式常返回 nan → 用 reqTickers 快照
  ② 实时需要 OPRA 订阅；无权限自动降到延迟（type 3）
  ③ 必须 qualifyContracts 才能取数据
  ④ 港股的合约乘数是 20

实时检测：
  reqMarketDataType(1) 后取一次，如果 bid/ask 为 nan → 自动切 3（延迟）

用法：
    with IBKRSource() as src:
        src.connect()
        spot, _ = src.spot("SOXS")
        chain = src.option_chain("SOXS", expiry, spot=spot)
        q = src.refresh_quotes(contracts)   # 只刷新指定合约（快）
"""
import math
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import config  # noqa: E402

config.setup_path()

try:
    from ib_async import IB, Stock, Option
except ImportError as e:
    raise ImportError(f"ib_async 未安装: {e}")


class IBKRSource:
    def __init__(self, host=None, port=None, client_id=None,
                 prefer_realtime=True):
        self.host = host or config.IBKR_HOST
        self.port = port or config.IBKR_PORT
        self.client_id = client_id or config.IBKR_CLIENT_ID
        self.prefer_realtime = prefer_realtime
        self.mdt = 1 if prefer_realtime else 3
        self.ib = IB()
        self._spot_cache = {}
        self._expiry_cache = {}
        self._strike_cache = {}
        self.mode = "unknown"

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *a):
        try:
            self.ib.disconnect()
        except Exception:
            pass

    # ---------- 连接 ----------
    def connect(self, timeout=15, detect_mode=True):
        self.ib.connect(self.host, self.port, clientId=self.client_id,
                        timeout=timeout, readonly=True)
        self.ib.reqMarketDataType(self.mdt)
        self.mode = "realtime" if self.mdt == 1 else "delayed"
        if detect_mode and self.prefer_realtime:
            self._detect_mode()
        return self

    def _detect_mode(self):
        """取一次 SPY 报价，判断是不是实时"""
        try:
            stk = Stock("SPY", "SMART", "USD")
            self.ib.qualifyContracts(stk)
            t = self.ib.reqMktData(stk, "", False, False)
            for _ in range(16):
                self.ib.sleep(0.5)
                if math.isfinite(t.bid) and math.isfinite(t.ask) and t.bid > 0:
                    break
            ok = (math.isfinite(t.bid) and math.isfinite(t.ask)
                  and t.bid > 0 and t.ask > 0)
            if not ok:
                # 切延迟
                self.ib.reqMarketDataType(3)
                self.mdt = 3
                self.mode = "delayed"
            else:
                self.mode = "realtime"
        except Exception:
            self.mode = "unknown"

    def is_connected(self):
        return self.ib.isConnected()

    def reconnect(self, tries=3):
        for i in range(tries):
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
            except Exception:
                pass
            try:
                self.connect()
                return True
            except Exception:
                time.sleep(5 * (i + 1))
        return False

    # ---------- 现货 ----------
    def spot(self, symbol, exch="SMART", cur="USD", tries=30, cache_s=5):
        """现价 + tick 对象（带 5 秒缓存）"""
        key = symbol
        now = time.time()
        c = self._spot_cache.get(key)
        if c and now - c["ts"] < cache_s:
            return c["px"], c["tick"]

        stk = Stock(symbol, exch, cur)
        self.ib.qualifyContracts(stk)
        t = self.ib.reqMktData(stk, "", False, False)
        px = None
        for _ in range(tries):
            self.ib.sleep(0.3)
            p = t.marketPrice()
            if math.isfinite(p) and p > 0:
                px = float(p)
                break
        if px is None and math.isfinite(t.close):
            px = float(t.close)
        self._spot_cache[key] = dict(px=px, tick=t, ts=now)
        return px, t

    # ---------- 到期/行权价（缓存） ----------
    def expiries(self, symbol):
        if symbol in self._expiry_cache:
            return self._expiry_cache[symbol]
        stk = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(stk)
        chains = self.ib.reqSecDefOptParams(symbol, "", "STK", stk.conId)
        if not chains:
            return []
        today = datetime.now().strftime("%Y%m%d")
        exps = sorted({e for c in chains for e in c.expirations if e >= today})
        self._expiry_cache[symbol] = exps
        return exps

    def strikes(self, symbol, expiry):
        key = (symbol, expiry)
        if key in self._strike_cache:
            return self._strike_cache[key]
        stk = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(stk)
        chains = self.ib.reqSecDefOptParams(symbol, "", "STK", stk.conId)
        out = []
        for c in chains:
            if expiry in c.expirations:
                out = sorted(c.strikes)
                break
        self._strike_cache[key] = out
        return out

    # ---------- 单合约 ----------
    def make_option(self, symbol, expiry, strike, right,
                    exch="SMART", cur="USD"):
        return Option(symbol, expiry, float(strike), right,
                      exch, currency=cur)

    def quote_option(self, contract):
        """取单个合约快照 → dict"""
        try:
            ts = self.ib.reqTickers(contract)
            if not ts:
                return None
            t = ts[0]
        except Exception:
            return None

        def num(x):
            try:
                v = float(x)
                return v if math.isfinite(v) else None
            except (TypeError, ValueError):
                return None

        bid, ask, last = num(t.bid), num(t.ask), num(t.last)
        mid = (bid + ask) / 2 if (bid and ask and bid > 0) else last
        iv = delta = gamma = vega = theta = None
        if t.modelGreeks:
            iv = num(t.modelGreeks.impliedVol)
            delta = num(t.modelGreeks.delta)
            gamma = num(t.modelGreeks.gamma)
            vega = num(t.modelGreeks.vega)
            theta = num(t.modelGreeks.theta)
        oi = None
        try:
            oi = num(t.callOpenInterest if contract.right == "C"
                     else t.putOpenInterest)
        except Exception:
            pass
        return dict(
            symbol=contract.symbol, exp=contract.lastTradeDateOrContractMonth,
            right=contract.right, strike=float(contract.strike),
            bid=bid, ask=ask, mid=mid,
            spread=(ask - bid) if (bid and ask) else None,
            spread_pct=((ask - bid) / mid * 100) if (bid and ask and mid) else None,
            iv=iv, delta=delta, gamma=gamma, vega=vega, theta=theta,
            oi=oi, volume=num(t.volume), last=last,
            liquid=bool(bid and ask and bid > 0 and mid
                        and (ask - bid) / mid < 0.15))

    # ---------- 期权链 ----------
    def option_chain(self, symbol, expiry, spot=None, n_strikes=20,
                     batch=8, right=None, delay=0.4):
        """取现价附近 n_strikes 个行权价的期权链"""
        if spot is None:
            spot, _ = self.spot(symbol)
        if spot is None:
            return []

        all_k = self.strikes(symbol, expiry)
        if not all_k:
            return []
        near = sorted(sorted(all_k, key=lambda k: abs(k - spot))[:n_strikes])
        rights = [right] if right else ["C", "P"]

        conts, meta = [], []
        for k in near:
            for r in rights:
                conts.append(self.make_option(symbol, expiry, k, r))
                meta.append((k, r))

        try:
            self.ib.qualifyContracts(*conts)
        except Exception:
            pass

        out = []
        for i in range(0, len(conts), batch):
            chunk = conts[i:i + batch]
            try:
                ts = self.ib.reqTickers(*chunk)
            except Exception:
                continue
            for c, t in zip(chunk, ts):
                def num(x):
                    try:
                        v = float(x)
                        return v if math.isfinite(v) else None
                    except (TypeError, ValueError):
                        return None
                bid, ask, last = num(t.bid), num(t.ask), num(t.last)
                mid = (bid + ask) / 2 if (bid and ask and bid > 0) else last
                iv = delta = gamma = vega = theta = None
                if t.modelGreeks:
                    iv = num(t.modelGreeks.impliedVol)
                    delta = num(t.modelGreeks.delta)
                    gamma = num(t.modelGreeks.gamma)
                    vega = num(t.modelGreeks.vega)
                    theta = num(t.modelGreeks.theta)
                oi = num(t.callOpenInterest if c.right == "C"
                         else t.putOpenInterest)
                out.append(dict(
                    symbol=symbol, exp=expiry, right=c.right,
                    strike=float(c.strike), bid=bid, ask=ask, mid=mid,
                    spread=(ask - bid) if (bid and ask) else None,
                    spread_pct=((ask - bid) / mid * 100)
                    if (bid and ask and mid) else None,
                    iv=iv, delta=delta, gamma=gamma, vega=vega, theta=theta,
                    oi=oi, volume=num(t.volume), last=last,
                    liquid=bool(bid and ask and bid > 0 and mid
                                and (ask - bid) / mid < 0.15)))
            self.ib.sleep(delay)
        return out


if __name__ == "__main__":
    print(f"连接 IBKR {config.IBKR_HOST}:{config.IBKR_PORT} ...")
    try:
        with IBKRSource() as src:
            print(f"  ✅ 已连接  模式: {src.mode}")
            for sym in ("SPY", "SOXS"):
                try:
                    px, _ = src.spot(sym)
                    print(f"  {sym} 现价 {px}")
                except Exception as e:
                    print(f"  {sym} 失败: {type(e).__name__}")
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {e}")
        print("  提示：需要打开 IB Gateway 并登录")
