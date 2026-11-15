# 每日波动率 & Gamma 结构报告

自动采集 CBOE 延迟期权数据，生成**每日收盘报告**，覆盖 Gamma Wall、攻防结构、成交量、波动率、衍生品跟随。

## 每日产物

| 文件 | 内容 |
|---|---|
| **`reports/daily_YYYYMMDD.md`** | 每日报告（Markdown） |
| **`reports/daily_YYYYMMDD.html`** | 每日报告（HTML，手机可看） |
| **`data/gex_history.csv`** | GEX 历史序列（回测用） |
| `reports/last_run.log` | 最近一次运行日志 |

## 报告结构

### 一、市场概览
SPY / QQQ / SMH / SOXX / VXX / SVIX / UVXY / VIXY 的：
现货、0DTE GEX、Gamma Flip、Call 墙、Put 墙、最大负 gamma、ATM IV

### 二、核心标的攻防结构
每个标的按到期列出：

| 到期 | DTE | 总 GEX | Flip | Call 墙 | Put 墙 | 最大负 gamma | ATM IV |

并给出判断：
- **体制**：负 GEX（波动放大）vs 正 GEX（波动收敛）
- **Gamma Flip**：跌破则进入负 gamma 区
- **最危险位置**：最大负 gamma 行权价
- **上方阻力 / 下方支撑**

### 三、成交量分析
- Call / Put 成交量
- **P/C 比**（>1.2 偏空，<0.6 偏多）
- 最活跃行权价 Top 4

### 四、波动率结构
- 0DTE IV / 近月 IV / 远月 IV
- **期限结构**：正挂（远月高）/ 倒挂（近月高）/ 平坦

### 五、衍生品跟随（VIX 家族）
VXX / SVIX / UVXY / VIXY 的现货、GEX、P/C、IV

### 六、交易信号
每个标的给出组合信号，例如：
- 🔴 强负 GEX
- 现价在最大负 gamma 下方 → 加速风险
- 突破 Call 墙 → 上方无阻力
- P/C 偏空/偏多

### 七、风险提示
明确标注数据限制（T-1 OI、GEX 符号假设、15 分钟延迟等）

## 运行

```bash
python daily_report.py
```

GitHub Actions 每天自动跑（见 `.github/workflows/daily.yml`）。

## gex_history.csv 字段

```
date, ts, symbol, spot, spot_raw, exp, dte,
total_gex, flip, call_wall, put_wall,
maxneg_k, maxneg_gex, call_oi_k, put_oi_k, n_contract
```

按 `(date, symbol, exp)` 去重，重复跑不会产生重复行。

## 读取示例

```python
import pandas as pd

df = pd.read_csv("data/gex_history.csv", parse_dates=["date"])

# 每日 0DTE 总 GEX
print(df[df.dte == 0].groupby(["date", "symbol"])["total_gex"].sum().unstack())

# 某标的墙位演变
spy = df[df.symbol == "SPY"]
print(spy.pivot_table(index="date", columns="dte", values="call_wall"))
```

## 数据源限制

| 项目 | 限制 |
|---|---|
| CBOE 延迟 | 15 分钟 |
| OI | T-1（昨日收盘持仓） |
| GEX 符号 | 假设做市商 call 多头 / put 空头 |
| 覆盖标的 | 仅 CBOE 有期权的标的 |

## 标的支持

| 标的 | 状态 |
|---|---|
| SPY / QQQ / SMH / SOXX | ✅ 已验证 |
| SOXL / VXX / UVIX / VIXY / UVXY / SVIX | ⚠️ 待验证（脚本会自动跳过失败项） |
