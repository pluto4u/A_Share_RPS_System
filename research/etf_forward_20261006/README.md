# 冻结方案的前瞻观察入口

实验：ETF-20261006-01-FWD-v1。当前完成的是数据留痕和月末目标权重记录，不是完整成交模拟或实盘系统。不应把初始100万元现金状态当作已经交易的收益曲线。

历史证据位于 output/etf_allocation_20261006，保持原样。观察记录独立写入 output/etf_forward_20261006：initialization.json、journal/、snapshots/。

## 已启用的检查

- 每次运行验证历史冻结清单与前瞻执行代码哈希，不允许原地修改规则后继续旧实验。
- 所有日志带实际UTC生成时间，附本地校验摘要和前一条记录摘要，文件不可由本程序覆盖。没有回拨日期参数。
- 本地摘要能发现普通改动，但不是第三方存证；拥有本机写权限的人仍可能重写全部文件。正式验证宜额外保存到独立只读存储或第三方时间戳服务，目前尚未启用。
- 采集真实东方财富未复权OHLC和同花顺分红，保留原始响应、来源、参数、采集时间及哈希。失败也留记录，绝不拿示例行情补齐。
- 16:00之前不消费当天日线。只在已核实交易日历中的当天月末、且数据采集完成后，生成四个模型的目标权重；次日才允许进入后续成交核验。
- 2026日历根据上交所休市安排制作。跨到2027年会停止，须核实新日历并发布新版，不能猜节假日。

## 在策略仓库运行

```powershell
Set-Location 'F:\88888.投资\08.选股\a_-share_-rps_-system'
python research/etf_forward_20261006/forward.py status
python research/etf_forward_20261006/forward.py collect
```

collect 返回本次快照绝对路径。校验该快照：

```powershell
python research/etf_forward_20261006/check_snapshot.py '本次快照路径'
```

当真实月末交易日收盘、重新 collect 成功后：

```powershell
python research/etf_forward_20261006/forward.py signal --snapshot '当天快照路径'
```

不是月末时会留下阻断日志，不产生任何目标或订单。过了月末再运行也不能补写成过去已生成信号；缺席必须作为缺席保留。
init 仅首次执行，当前已初始化，重复执行会拒绝覆盖。没有后台调度任务；当前工具需要手动执行，并不会自行在未来醒来。

## 当前尚未完成

目前没有券商连接，也没有记录任何成交。下阶段需要独立的竞价/成交采集、逐笔现金与份额账本、分红与证券状态的公告级复核，然后才可报告前瞻模拟收益。日线开盘代理不能冒充实时成交。

首次符合规则的目标记录预计2026-10-30收盘后；10月8日起正常开市。来源：
[上交所2026年休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml)、
[国庆休市公告](https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml)。若交易所临时变更安排，应停止并重新核实，不直接覆盖冻结日历。

测试：python -m pytest tests/test_etf_forward.py tests/test_etf_allocation_study.py -q -o addopts=
