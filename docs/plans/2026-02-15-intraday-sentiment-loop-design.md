# 盘中周期情绪判断设计

**日期**: 2026-02-15  
**范围**: 在现有 `market_gambler_watch.py` 增加盘中周期运行能力（15分钟一轮）

## 需求确认

- 默认周期：15分钟
- 推送策略：每轮都飞书推送
- 运行时段：仅工作日交易时段（09:30-11:30，13:00-15:00）

## 设计目标

1. 最小改动复用现有单轮分析流程
2. 增加循环调度能力，同时保留原有单次运行行为
3. 在非交易时段自动跳过，避免无意义采集和推送
4. 循环模式下遇到单轮异常不中断整体服务

## 方案选择

采用“单文件增强”方案：直接在 `market_gambler_watch.py` 增加循环与交易时段守卫。

原因：
- 与当前调用链（`cloud_runner.py` / GitHub Actions）兼容最好
- 不引入新入口，部署和排障简单
- 对现有逻辑改动小，风险可控

## 架构设计

### 1) 单轮执行函数抽取

将当前 `main()` 内核心流程抽取为 `run_once(full: bool, feishu: bool)`：
- 负责执行一次完整采集、分析、展示、推送
- 返回结构化结果（至少包含 `status`, `timestamp`）

`main()` 仅负责参数解析和运行模式分发。

### 2) 交易时段判定

新增 `is_trading_session(now: datetime | None = None) -> bool`：
- 周一到周五
- 09:30 <= t <= 11:30 或 13:00 <= t <= 15:00

非交易时段在循环模式中打印“跳过本轮”并等待下个周期。

### 3) 循环调度

新增 CLI 参数：
- `--loop`：启用循环模式
- `--interval-minutes`：循环周期（默认 15）
- `--session-only`：仅交易时段运行（默认 false，开启后启用时段过滤）

循环行为：
- `while True` 按周期执行
- 每轮 `try/except` 捕获异常并继续下一轮
- 支持 `KeyboardInterrupt` 优雅退出

### 4) 向后兼容

- 不传 `--loop` 时行为与当前一致（执行一次）
- 现有 `--full` / `--feishu` 语义不变

## 测试设计

新增测试文件：`tests/test_intraday_loop.py`

覆盖点：
1. `is_trading_session()` 在交易时段返回 `True`
2. `is_trading_session()` 在午休/非交易日返回 `False`
3. 循环模式 + `session_only=True` + 非交易时段 => 不调用 `run_once`
4. 循环模式在交易时段会调用 `run_once`

测试策略：
- 使用 monkeypatch 替换时间函数与 `run_once`
- 使用可控循环终止条件（例如新增 `max_loops` 内部参数仅供测试）

## 风险与缓解

- **风险**：无限循环难测  
  **缓解**：引入可测试的 `run_loop(..., max_loops=None)` 函数，生产默认 `None`
- **风险**：交易时段边界误差  
  **缓解**：边界时点（09:30, 11:30, 13:00, 15:00）单元测试覆盖
- **风险**：单轮异常导致整体停止  
  **缓解**：循环层捕获异常并打印 traceback，下一轮继续

## 运维使用

- 盘中循环：
  - `python market_gambler_watch.py --loop --interval-minutes 15 --session-only --feishu`
- 盘中完整模式（更耗时）：
  - `python market_gambler_watch.py --loop --interval-minutes 15 --session-only --full --feishu`
