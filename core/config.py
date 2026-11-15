"""
core/config.py — 全局配置

所有路径、常量集中在这里，避免脚本里散落硬编码。
"""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------- 路径 ----------
ROOT = HERE
DATA_DIR = os.path.join(HERE, "data")
REPORT_DIR = os.path.join(HERE, "reports")
LIB_DIR = os.path.join(HERE, ".pylibs")
HOME_FAKE = os.path.join(HERE, "fakehome")
DB_PATH = os.path.join(DATA_DIR, "market.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(HOME_FAKE, exist_ok=True)

# 让 futu SDK 的日志写到 fakehome（沙箱里不能写真实 AppData）
os.environ["USERPROFILE"] = HOME_FAKE   # 必须强制覆盖：futu SDK 会在家目录写日志
os.environ["APPDATA"] = HOME_FAKE

# ---------- 数据源 ----------
FUTU_HOST = os.environ.get("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.environ.get("FUTU_PORT", "11111"))
IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "4001"))
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "250"))

CBOE_BASE = "https://cdn-api.cboe.com/api/global/delayed_quotes/options"
CBOE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0 Safari/537.36"}

# ---------- 标的 ----------
CORE_SYMBOLS = ["SPY", "QQQ", "SMH", "SOXX"]
DERIV_SYMBOLS = ["VXX", "SVIX", "UVXY", "VIXY"]
VIX_FUTURES = ["US.VX2611", "US.VX2612", "US.VX2701",
               "US.VX2702", "US.VX2703", "US.VX2706"]

# ---------- 交易成本 ----------
SLIPPAGE_INTRADAY = 0.01      # 盘中滑点（美元/股）
SLIPPAGE_CLOSE = 0.10         # 收盘时滑点（保守）
COMMISSION_PER_CONTRACT = 1.00  # 美股期权每张每边

# ---------- 风控 ----------
MAX_LOSS_PER_TRADE_PCT = 0.01   # 单笔最大亏损 1%
MAX_DAILY_LOSS_PCT = 0.03       # 单日最大亏损 3%
MAX_POSITION_PCT = 0.20         # 单品种最大仓位 20%


def setup_path():
    """把 .pylibs 加入 sys.path"""
    import sys
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)

