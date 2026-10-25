# 策略研发工作区约定

2026-10-24主目录全文件整理：当前代码/数据/产物边界见
[docs/PROJECT-ORGANIZATION.md](docs/PROJECT-ORGANIZATION.md)，完整运行入口见
[docs/ENTRYPOINTS.md](docs/ENTRYPOINTS.md)。旧路径通过兼容映射保留；`research/experiments/`
是原根目录实验脚本的正式位置。以下早期整合记录作为历史上下文保留。

本仓库是 A 股 RPS、右侧趋势与选股研究的唯一代码研发源。日常源码、通用配置、方法文档和测试只在这里维护；应用、原始材料、个人数据、行情缓存及实验输出不因本次整合而搬入仓库。

## 相邻组件

- `F:\88888.投资\08.选股\tick-stock-panel`：独立的量化工作台应用。通过 `scripts/refresh_daily_observation.py` 读取冻结策略实现和审计快照，发布只读研究观察数据。
- `F:\88888.投资\08.选股\07.investment_research\right_side_strategy_bundle_20260924_221054\right_side_strategy`：整合前策略包的完整兼容副本，当前保留冻结审计、缓存与历史研究产物；不是代码主目录。
- `F:\88888.投资\08.选股\07.investment_research` 根目录：独立的产业链证据管线。策略信号不会自动转成产业链事实或证据。

## 同名文件冲突

整合时遇到的同路径差异文件没有自动覆盖：

- `skills/a-share-rotation-trader/references/data-availability.md`
- `skills/a-share-rotation-trader/scripts/verify_qfq.py`
- 根 `README.md` 与 `docs/tdx_docs_index.md`（在兼容副本中添加过目录关系说明）

需要合并这些差异时逐个比较语义后再决定。同步兼容包时遵循“只增量，不覆盖”；不得把 `output/`、`data/`、个人研究输入、运行日志或密钥配置当作源码同步。导入工程中的第二份策略包已与保留的历史兼容包逐文件哈希确认完全相同，故已清理重复副本。

## TickFlow 冻结研究接口

TickFlow 刷新脚本优先接受 `--research-root`，也支持环境变量 `RPS_RESEARCH_ROOT`。未指定时，会先检查本仓库，再回退到上方第一份兼容包；当前本仓库未承载冻结审计与缓存，因此既有 TickFlow 观察页仍使用兼容包中的 `trend_start/`、`output/daily_system/audit.json` 和匹配行情缓存。策略代码维护在此仓库，历史冻结结果仍留在原位置，直到经过审阅后有意迁移。

本次目录整合没有运行策略测试或回测，也没有改变策略参数、样本或绩效结论。
