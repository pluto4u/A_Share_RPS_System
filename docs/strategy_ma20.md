# 20 日均线趋势选股策略

## 1. 策略目的

MA20 约等于一个交易月的平均持仓成本。本策略不把“收盘价站上 MA20”直接视为买点，而是先确认中期趋势，再识别两个可执行形态：

1. **放量收复 MA20**：调整后重新站回一个月平均成本，资金主动回流。
2. **缩量回踩 MA20**：趋势已经形成，回踩时抛压缩小，并在均线附近收回。

其余仍在 MA20 上方、但没有出现明确触发的股票只进入“趋势观察”，不等同于建议买入。

## 2. 数据来源与质量门槛

入口 `scripts/screening/run_ma20_screener.py` 只读取本地通达信：

```text
C:\new_tdx\vipdoc\sh\lday\shXXXXXX.day
C:\new_tdx\vipdoc\sz\lday\szXXXXXX.day
```

使用字段：日期、开高低收、成交量、成交额。没有调用网络行情。

数据质检规则：

- 目标日不是简单取全表最大日期，而是取最近日期中覆盖率达到 90% 的最新交易日，防止单个错误未来日期污染结果；
- 个股最后一根 K 线必须等于目标日，停牌或陈旧数据不参与；
- 最近计算窗口不得有重复日期、非有限数、非正价格、负成交量/成交额或 OHLC 关系错误；
- 任一异常会整只排除，不用错误数据继续计算；
- 生成独立的 `*_audit.json`，记录排除数量与最终有效股票数。

通达信 `.day` 没有股票名称、ST 标记、流通股本和真实换手率。入口会另外读取本地 `T0002/hq_cache/{shs,szs}.tnf` 名称表，默认排除名称以 ST / *ST 开头或含“退”的股票；也可继续通过 `--exclude-codes` 维护额外排除清单。名称不参与信号计算。

## 3. 默认选股条件

### 3.1 公共趋势条件

所有候选必须同时满足：

- `MA20 >= MA60`；
- `MA20 >= 5 个交易日前的 MA20`；
- `close >= MA20`；
- `close / MA20 - 1 <= 6%`，避免追高；
- 单日涨幅不超过 7%，5 日涨幅不超过 15%，60 日涨幅不超过 60%；
- 20 日平均成交额不少于 8000 万元；
- 至少 80 根有效日线，且目标日有成交。

### 3.2 放量收复 MA20

- 昨日收盘价不高于昨日 MA20；
- 今日收盘价重新站上 MA20；
- 今日收阳且收盘高于昨日；
- 收盘距 MA20 不超过 3.5%；
- 当日成交量为 20 日均量的 1.2～3.5 倍。

这是偏右侧的确认信号。第二天若跳空高开超过约 3%，放弃追价，等待回踩。

### 3.3 缩量回踩 MA20

- 昨日仍在 MA20 上方；
- 最近 10 日曾至少高于 MA20 约 4%，证明此前有一段上攻；
- 今日最低价触及 MA20 上方 1.5%以内，收盘仍在 MA20 上方；
- 收盘距 MA20 不超过 2.5%；
- 今日收阳且收盘高于昨日；
- 当日成交量不超过 20 日均量的 95%。

这是趋势中的低风险回踩信号，失效位置比追涨买点更清楚。

## 4. 执行与退出纪律

- 信号在收盘后生成，下一交易日执行；不使用当日收盘后才知道的数据假设盘中成交。
- 次日高开超过信号日收盘 3%不追；跌破信号日最低价则取消。
- 初始风险线参考 `MA20 × 0.98` 与信号日最低价，使用两者中更严格的一条。
- 收盘连续两日低于 MA20，退出趋势仓；单笔硬止损不得宽于买入价的 7%。
- 单笔计划亏损不超过账户净资产的 0.5%，单只股票仓位上限 15%。
- 同一行业最多选择 2 只，行业合计仓位不超过 30%，防止技术信号在板块共振时形成隐性满仓。
- 浮盈达到 10%后把风险线上移至成本附近；明显偏离 MA20 超过 15%时分批兑现，不再加仓。

## 5. 运行方法

扫描本地数据中的最新可靠交易日：

```powershell
python scripts/screening/run_ma20_screener.py
```

指定日期；若为周末或节假日，会自动回退到此前最新可靠交易日：

```powershell
python scripts/screening/run_ma20_screener.py --date 20260731
```

只保留两个候选信号，不输出观察档：

```powershell
python scripts/screening/run_ma20_screener.py --candidates-only
```

排除科创板、创业板并读取 ST 清单：

```powershell
python scripts/screening/run_ma20_screener.py --exclude-boards 688,300,301 --exclude-codes output/st_list.csv
```

## 6. 输出

```text
output/ma20_screener_YYYYMMDD.csv
output/ma20_screener_YYYYMMDD_BUY.csv
output/ma20_screener_YYYYMMDD_BUY_codes.csv
output/ma20_screener_YYYYMMDD_WATCH.csv
output/ma20_screener_YYYYMMDD_WATCH_codes.csv
output/ma20_screener_YYYYMMDD_audit.json
```

其中 `stop_reference` 是 `MA20 × 0.98` 的机械参考线；`score` 只用于同类信号排序，不代表预期收益率。
