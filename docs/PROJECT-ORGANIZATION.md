# 主工程文件归类与使用入口

正式工作目录为 `F:/88888.投资/08.选股/a_-share_-rps_-system`。此次整理覆盖全部项目文件的
分类清单，代码按实际依赖归并，数据、历史结果和参考材料均保留。

## 从哪里开始

- 日常操作：根目录的稳定入口，如 `scripts/daily/run_daily_system.py`、`scripts/screening/run_tdx_screener.py`、`scripts/daily/run_combined_daily.py`。
- 所有运行入口：[ENTRYPOINTS.md](ENTRYPOINTS.md)，按日常工作流、筛选、研究、验证、报告、集成归类。
- 策略档案：[../strategy_archive/INDEX.md](../strategy_archive/INDEX.md)。该档案只覆盖前次归档范围，
  不宣称已验证本主仓库新增的全部研究方案；完整代码入口见运行索引。
- 使用说明集中在 [guides/](guides/)，原有历史计划保留于 `plans/`。
- 全文件机器清单：`outputs/catalog/files.json`、`files.csv`。
- 精确哈希重复清单：`outputs/catalog/duplicates.json`。

## 功能代码

- `rps_analyzer/`：TDX读取、复权、RPS和筛选模型。
- `trend_start/`：趋势、回测、组合及退出研究核心。
- `macro/`：宏观状态和市场环境判断。
- `industry_chain/`、`research_pipeline/`：产业链与证据研究管线。
- `emotion_validation/`：情绪因子、数据层、ETF轮动和验证。
- `workflows/`：本地通达信宽度计算与内容出图。
- `research/experiments/`：原根目录29个 `exp_*.py` 实验，内部和生产调用已更新为包导入。
- `research/<主题日期>/`：已有专题研究；与实验共享代码不以文件名相近为由合并策略规则。
- `research/legacy_workspace/`：原 `tmp/`，包含仍有调用的日报、绘图、研究脚本及历史输入。
- `tools/`：统一入口、档案、盘点、整理与诊断工具。
- `tests/`：可导入的项目测试包；真实文件验证辅助函数不再被pytest误收集为fixture测试。

根目录生产脚本保留，原因是已有调度、测试、第三方调用和源码哈希审计引用这些文件。
全部入口已经集中登记，无需依靠根目录文件名猜用途。

## 数据、结果与应用

- `data/`：本地DuckDB、行情与运行数据；不与代码或历史研究结果合并。
- `outputs/workflows/`：市场宽度图片、文案、逐次运行证据。
- `outputs/signals/`、`outputs/exports/`：信号和交付文件。
- `outputs/logs/legacy/`：原 `logs/`；根目录旧标准输出移到 `outputs/logs/root/`。
- `outputs/strategies/`：统一访问原 `output/` 的策略结果。
- `apps/reports/`：原 `reports/` 中的完整报告应用；内部文件未改变。
- `docs/references/`、`docs/learning/`：原始参考文件和学习资料。
- `research_inputs/tdx_blocks/`：通达信历史板块文件。
- `skills/serenity/`：归并到技能目录的现有技能及参考资料。

Windows目录兼容映射保留 `tmp/`、`reports/`、`logs/`、`reference_doc/`、`learn/`、
`08.tdx_qushi/`、`serenity-skill/` 的旧路径，均指向上述实际目录。`output/` 因进程占用保留
为实际目录，`outputs/strategies/` 是指向它的映射。映射不产生第二份数据，也不修改历史文件。
跨机器复制工程时应复制实际目录并重新建立映射，不能把两个入口各复制成一份数据。

## 清理与合并的边界

根目录15份使用文档已集中迁到 `docs/guides/`，项目Markdown链接已重定位。
29份实验脚本集中为研究包，公共依赖统一从 `research.experiments` 导入；实验正文和策略参数保持不变。
标准输出文件从根目录归入日志/信号；Python缓存移到同级清理备份，可恢复。

精确重复文件约2MB，主要是必要源码快照、应用构建数据、依赖元数据和不同日期相同内容的结果。
这些文件没有按“内容相同”直接删除，重复清单明确标记保留原因，避免破坏回溯和运行契约。
未永久删除行情、账户材料、研究输入、已生成结果或原策略实现。

整理后的清单登记4285个非缓存文件、约5.63GB，包含1294个本地环境文件；Git内部数据、
Python缓存、标准虚拟环境与node_modules不作为项目业务文件重复登记。
根目录47个散落文件已归位，建立8条兼容目录映射，162个再生缓存目录移入清理备份。
当前运行入口共139项。全量测试基线及原有5项失败详见
[TEST-BASELINE-20261024.md](TEST-BASELINE-20261024.md)。

原 `manifest.json` 与 `scripts/validation/verify_package.py` 属于2026-09-24历史分发包，不是当前工程完整性基线；
当前布局以实时文件清单、Git检查点及清理日志为准。

## 统一命令

```powershell
# 更新全文件、重复项和运行入口索引
python tools/workspace.py catalog
# 查看日常入口
python tools/workspace.py entries --category daily_workflow
# 查看研究入口
python tools/workspace.py entries --category research
# 调用已有入口，-- 后面的参数原样传给脚本
python tools/workspace.py run --entrypoint run_tdx_screener -- --help
# 新位置的实验入口
python -m research.experiments.exp_weekly_rps
# 通达信数据到小红书图片
python tools/workspace.py breadth
```

## 恢复

清理备份目录为项目同级 `a_-share_-rps_-system_cleanup_20261024T162617/`，含变更前文件、
缓存、Git差异和逐步操作日志。需要撤销本次布局时，在当前工程根目录执行：

```powershell
python tools/organize_project.py restore --journal "F:/88888.投资/08.选股/a_-share_-rps_-system_cleanup_20261024T162617/journal.json"
```

恢复仅处理本次日志中的移动与编辑；整理后新生成的文档、索引和工作产物不会自动删除。
