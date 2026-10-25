# 面向AI工程的策略归档

2026-10-24补充：更新代码后先执行 `python tools/review_strategy_archive.py`。
它把主源码漂移审阅和新模块队列写入 `strategy_archive/reviews/<运行ID>/`，不覆盖旧卡。
`unchanged` 只表示主文件哈希相同，不表示依赖、规则或收益已验证。
重新回测使用 [回测协议](REBACKTEST-PLAN-20261024.md)；先人工复核规则，再决定是否重建卡片。

本次建立53个条目：主工程27项（含基准、风控、研究实验），面板26项内置策略。
产业研究工程是证据与瓶颈评分管线，不列作量化买卖策略；其中空的旧策略包目录没有可核验代码。

## 存储约定

采用“Markdown + JSON注册表 + SQLite检索索引 + 内容哈希快照”。完全本地可用，
人和AI读取同一来源，无需先上向量数据库或另行部署服务。

- `strategy_archive/INDEX.md`：人工浏览入口。
- `strategy_archive/cards/<稳定ID>.md`：一策略一卡，可由AI单独读取。
- `strategy_archive/registry.json`：结构化规则、状态、参数、来源与哈希，供程序和AI读取。
- `strategy_archive/sources/<SHA256>.py`：按规范化UTF-8源码内容寻址；同源码去重，旧快照保留。
- `outputs/catalog/strategies.sqlite`：可重建的SQLite FTS5索引，支持中文三元字符索引；CLI兼容短词搜索。
- `outputs/workflows/tdx_breadth/<数据日期>/<运行ID>/`：每次独立保存的宽度数据、图片、文案与证据清单。

策略规则、档案和工作流代码进入Git；数据库索引、行情与运行产物不入Git。
长期运行可把 `A_SHARE_OUTPUT_DIR` 指向数据磁盘，再对整个输出目录做版本化备份；已有TOS同步
仅覆盖原有清单，不会自动上传这些新内容。需要备份原始通达信文件时单独备份 `vipdoc`。

## AI检索流程

1. 先读取注册表，按组件、状态和名称筛选。
2. 读取对应卡片，确认规则、限制和源文件。
3. 需要修改或审查时比对当前源码与归档SHA256；快照不自动代替当前实现。
4. 需要引用指标时读取对应运行的 `summary.json` 和 `manifest.json`，以 `as_of` 为行情日期。
5. 任何收益结论均需新的验证记录；`performance_verified=false` 不能由旧注释覆盖。

```powershell
python tools/workspace.py archive
python tools/strategy_archive.py search 龙回头
python tools/strategy_archive.py search 回踩
```

`archive` 更新卡片、注册表与索引，保留已有内容寻址源码快照。对外部面板只读源码，
不会导入其模块、改变其依赖或启动服务。

## 状态解释

- `locally_executed`：已使用真实本地数据运行，日期见运行结果。
- `implemented_requires_data`：有实现与入口，完整运行需要对应行情、配置或网络。
- `implemented_overlay`：可用的风控函数，作为策略附加层。
- `research_requires_revalidation`：研究实现，仍需口径修正、前视审查或样本外验证。
- `research_optimizer`：参数搜索工具，不是独立实盘策略。
- `source_present_not_runtime_verified`：归档了面板源码与参数，本次未运行其策略引擎。

当前可直接使用的是通达信宽度内容工作流；RPS、每日S5、ETF轮动、龙回头、口袋支点和回踩
具备代码实现，但不能在缺少输入的情况下称为已验收实盘可用。杯柄的居中极值、短线策略
的ST/北交所涨停口径，以及S2/S5结论冲突，已明确记录到各自卡片。
