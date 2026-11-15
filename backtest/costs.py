"""
backtest/costs.py — 交易成本模型

所有回测必须扣成本，否则结果虚高。
"""
import sys
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import config  # noqa: E402


def option_cost(contracts, price, side, slippage=None, commission=None):
    """单腿期权成本

    Args:
        contracts: 张数（正数）
        price:     理论成交价（通常用中间价）
        side:      'buy' 或 'sell'
        slippage:  每股滑点（默认用盘中 0.01）
        commission:每张每边佣金（默认 $1）

    Returns:
        实际成交价、总成本（正数=支出，负数=收入）
    """
    slip = config.SLIPPAGE_INTRADAY if slippage is None else slippage
    comm = config.COMMISSION_PER_CONTRACT if commission is None else commission

    if side == "buy":
        px = price + slip
        cash = -contracts * px * 100
    else:
        px = price - slip
        cash = contracts * px * 100
    fee = contracts * comm
    return px, cash - fee


def spread_cost(legs, slippage=None, commission=None):
    """整个组合的净现金流

    legs: [(contracts, price, 'buy'|'sell'), ...]
    Returns: 净现金（正=收入，负=支出）
    """
    total = 0.0
    for contracts, price, side in legs:
        _, cash = option_cost(contracts, price, side, slippage, commission)
        total += cash
    return total


def round_trip_fee(contracts, commission=None):
    """开仓 + 平仓的总手续费"""
    comm = config.COMMISSION_PER_CONTRACT if commission is None else commission
    return contracts * comm * 2


if __name__ == "__main__":
    print("成本模型自测\n")
    # 买 10 张 @3.11，卖 8 张 @4.33
    legs = [(10, 3.11, "buy"), (8, 4.33, "sell")]
    cash = spread_cost(legs)
    print(f"买10×3.11 / 卖8×4.33 → 净现金 {cash:+,.0f}")
    print(f"  手续费单边 {(10+8)*1.0:,.0f}，双边 {(10+8)*2:,.0f}")

    print("\n对比收盘价差（0.10 滑点）：")
    cash2 = spread_cost(legs, slippage=config.SLIPPAGE_CLOSE)
    print(f"  净现金 {cash2:+,.0f}（比盘中少 {cash-cash2:,.0f}）")
