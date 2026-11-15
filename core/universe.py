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
