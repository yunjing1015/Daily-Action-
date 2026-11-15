"""
core/db.py — 统一数据层（SQLite）

所有市场数据、信号、交易记录都进这一个文件：data/market.db

表结构：
  bars          日线（symbol, date）
  option_chain  期权链快照（ts, symbol, exp, right, strike）
  gex_daily     GEX 序列（date, symbol, exp）
  oi_walls      OI 墙位快照（date, symbol, exp, right, strike, oi）
  intraday      盘中记录（ts, symbol, spot, ...）
  signals       策略信号
  trades        交易记录
  meta          键值元数据
"""
import os
import sqlite3
from contextlib import contextmanager

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(HERE, "data", "market.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS option_chain (
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exp TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    bid REAL, ask REAL, mid REAL,
    oi REAL, volume REAL,
    iv REAL, delta REAL, gamma REAL, vega REAL, theta REAL,
    source TEXT,
    PRIMARY KEY (ts, symbol, exp, right, strike)
);

CREATE TABLE IF NOT EXISTS gex_daily (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exp TEXT NOT NULL,
    dte INTEGER,
    spot REAL,
    total_gex REAL,
    flip REAL,
    call_wall REAL,
    put_wall REAL,
    maxneg_k REAL,
    maxneg_gex REAL,
    call_oi_k INTEGER,
    put_oi_k INTEGER,
    n_contract INTEGER,
    PRIMARY KEY (date, symbol, exp)
);

CREATE TABLE IF NOT EXISTS oi_walls (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exp TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    oi REAL,
    PRIMARY KEY (date, symbol, exp, right, strike)
);

CREATE TABLE IF NOT EXISTS intraday (
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    spot REAL,
    data TEXT,              -- 原始 JSON 行
    PRIMARY KEY (ts, symbol)
);

CREATE TABLE IF NOT EXISTS signals (
    ts TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal TEXT,
    score REAL,
    note TEXT,
    PRIMARY KEY (ts, strategy, symbol)
);

CREATE TABLE IF NOT EXISTS trades (
    ts TEXT NOT NULL,
    strategy TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL,
    price REAL,
    fee REAL,
    note TEXT,
    PRIMARY KEY (ts, symbol, side)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_bars_sym_date ON bars(symbol, date);
CREATE INDEX IF NOT EXISTS idx_gex_date ON gex_daily(date, symbol);
CREATE INDEX IF NOT EXISTS idx_chain_sym_exp ON option_chain(symbol, exp);
"""


@contextmanager
def connect(path=None):
    """with connect() as conn: ..."""
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(path=None):
    """建表（幂等）"""
    with connect(path) as conn:
        conn.executescript(SCHEMA)
    return path or DB_PATH


def upsert(conn, table, rows, key_cols=None):
    """批量 upsert（INSERT OR REPLACE），返回写入行数"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (f"INSERT OR REPLACE INTO {table} "
           f"({','.join(cols)}) VALUES ({','.join('?' * len(cols))})")
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    return len(rows)


def stats(path=None):
    """各表行数"""
    with connect(path) as conn:
        out = {}
        for t in ("bars", "option_chain", "gex_daily", "oi_walls",
                  "intraday", "signals", "trades"):
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = 0
        return out


if __name__ == "__main__":
    p = init()
    print(f"数据库已初始化: {p}")
    for k, v in stats().items():
        print(f"  {k:14s} {v:>8,} 行")

