"""
core/universe.py — 标的池定义

原则（按你的要求）：
  · 只放熟悉、流动性好、有期权的
  · 覆盖代表性行业
  · 控制在 20 个以内

新增标的只需改这里，所有脚本自动生效。
"""

# ---------- 核心池：深度分析（GEX / 期权链 / IV） ----------
CORE = {
    "SPY":  "标普500（大盘基准）",
    "QQQ":  "纳指100（科技基准）",
    "SMH":  "半导体（英伟达产业链）",
    "SOXX": "半导体（费城指数）",
}

# ---------- 波动率池：衍生品联动 ----------
VOL = {
    "VIX":  "恐慌指数（现货）",
    "VXX":  "做多VIX短期期货",
    "SVIX": "做空VIX短期期货",
    "UVXY": "1.5倍做多VIX期货",
}

# ---------- 行业池：板块轮动 ----------
SECTOR = {
    "XLK": "科技",
    "XLF": "金融",
    "XLE": "能源",
    "XLV": "医疗",
    "XLI": "工业",
    "XLY": "可选消费",
}

# ---------- 个股池：龙头 ----------
MEGA = {
    "NVDA": "英伟达",
    "AAPL": "苹果",
    "MSFT": "微软",
    "TSLA": "特斯拉",
    "META": "Meta",
}

# ---------- 指数池：宽基 ----------
INDEX = {
    "IWM": "罗素2000（小盘）",
    "DIA": "道指",
}

# 全部标的（富途代码）
def all_codes():
    out = []
    for grp in (CORE, VOL, SECTOR, MEGA, INDEX):
        for s in grp:
            out.append("US..VIX" if s == "VIX" else f"US.{s}")
    return out


def all_symbols():
    out = []
    for grp in (CORE, VOL, SECTOR, MEGA, INDEX):
        out.extend(grp.keys())
    return out


def group_of(sym):
    for name, grp in (("核心", CORE), ("波动率", VOL), ("行业", SECTOR),
                      ("龙头", MEGA), ("指数", INDEX)):
        if sym in grp:
            return name
    return "其他"


def to_futu(sym):
    return "US..VIX" if sym == "VIX" else f"US.{sym}"


if __name__ == "__main__":
    total = 0
    for name, grp in (("核心", CORE), ("波动率", VOL), ("行业", SECTOR),
                      ("龙头", MEGA), ("指数", INDEX)):
        print(f"\n{name}池（{len(grp)} 个）：")
        for s, d in grp.items():
            print(f"  {s:6s} {d}")
        total += len(grp)
    print(f"\n合计 {total} 个标的")

# ---------- 扫描池：全市场候选（流动性好的美股 + ETF） ----------
# 选取标准：日均成交额 > $50M，有活跃期权
SCAN_POOL = [
    # 大盘 ETF
    "SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "VTI",
    # 行业 ETF
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "SMH", "SOXX", "XBI", "KRE", "XRT", "XHB", "GDX", "ITB", "TAN", "IBB",
    # 杠杆 ETF
    "TQQQ", "SQQQ", "SOXL", "SOXS", "SPXL", "SPXS", "UVXY", "SVIX", "VXX", "VIXY",
    "TLT", "TBT", "HYG", "JNK", "FXI", "KWEB",
    # 科技龙头
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL",
    "CRM", "ADBE", "NFLX", "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX",
    "KLAC", "ASML", "TSM", "ARM", "SMCI", "MRVL", "ON", "ADI",
    # 软件/AI
    "PLTR", "SNOW", "CRWD", "DDOG", "NET", "MDB", "PANW", "ZS", "OKTA", "AI",
    "MU", "COIN", "HOOD", "SQ", "PYPL", "SHOP",
    # 金融
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "V", "MA", "AXP",
    "BRK-B", "USB", "PNC", "TFC",
    # 医疗
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "CVS", "CI", "ELV",
    # 消费
    "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "KO", "PEP",
    "PG", "CL", "MDLZ", "PM", "MO",
    # 能源/工业/材料
    "XOM", "CVX", "COP", "SLB", "OXY", "PSX", "VLO", "MPC",
    "CAT", "DE", "BA", "GE", "HON", "LMT", "RTX", "UNP", "UPS", "FDX",
    "LIN", "FCX", "NEM", "NUE", "DOW",
    # 通信/公用
    "T", "VZ", "TMUS", "CMCSA", "DIS", "NEE", "DUK", "SO", "AEP",
    # 中概
    "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI", "TCOM",
    # 其他热门
    "GME", "AMC", "PLTR", "RIVN", "LCID", "F", "GM", "UBER", "LYFT",
    "ABNB", "DASH", "RBLX", "U", "SNAP", "PINS", "SPOT", "ROKU",
]

# 去重
SCAN_POOL = sorted(set(SCAN_POOL))
