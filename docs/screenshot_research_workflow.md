# 截图池产业链与RPS研究流程

本流程把截图、知识星球、聊天记录和公众号中的证券线索，变成可追溯的批次、产业链位置、每日趋势快照和两级报告。候选名单是研究入口，不把颜色、转发次数或大V结论直接当成买入信号。

## 1. 固定产物

| 层级 | 位置 | 作用 |
|---|---|---|
| 原始来源 | `docs/research/source_materials/` | 保存原图和原始材料，不覆盖、不二次改写 |
| 批次证券池 | `research_inputs/*_screenshot_pool.json` | 记录来源、证券代码、日期、代码校正和市场温度 |
| 产业分类 | `research_inputs/chain_classification_*.json` | 记录主题、产业节点、主位置、次位置和ETF |
| 结构化数据库 | `data/industry_chain.db` | 保存批次、公司、产业链、观点、证据、事件和趋势历史 |
| 简报 | `output/research_batches/*_brief.md` | 只回答当前能否执行、哪些不能追、下一批观察什么 |
| 详细报告 | `output/research_batches/*_detailed.md` | 保存全量分类、趋势状态、数据缺口与迭代方法 |
| 全量明细 | `output/research_batches/*_full.csv` | 供筛选、复盘和后续程序使用 |

## 2. 信息汇总口径

知识星球、聊天资料、公众号和卖方材料先保留原文，再拆成三类记录：

1. `evidence`：公司公告、财报、招股书、客户认证、产能和价格等可核验事实。
2. `viewpoints`：卖方或大V的判断、估值、预测和推荐理由，必须记录来源日期、采纳状态、验证条件及反证条件。
3. `events`：财报、新品、扩产、涨价、客户导入等催化剂，以及延期、降价、良率和需求下修等风险。

同一结论可以有多个来源，但转述不能重复计为多条独立证据。未经公司披露或产业数据验证的说法只作为待核验观点，不提高证据质量。

## 3. 产业链摆放规则

- 每只非ETF证券必须有且只有一个主位置，保证统计不重复。
- 存在真实跨环节业务时可增加次位置，不能仅凭概念标签建立映射。
- 主位置回答主要利润来源或最需要验证的业务；次位置回答潜在增量。
- 产业链映射必须继续用收入占比、客户、订单、产能、良率和认证验证。
- 港股与北交所可以进入产业图谱，但没有同口径行情时不与沪深A股RPS混排。

## 4. 趋势与执行状态

| 状态 | 含义 | 处理 |
|---|---|---|
| `SYSTEM_BUYABLE` | 同日L1与L2共振、站上MA60且靠近MA20 | 才进入可执行清单 |
| `SYSTEM_WATCH` | 已入L1，但位置或更严格信号未齐 | 继续观察，不等同买入 |
| `WAIT_PULLBACK_EXTENDED` | 已入L1但20日涨幅或MA60乖离过大 | 不追，等待回踩 |
| `WATCH_NEAR_TRIGGER` | RPS120和RPS250双强，站上MA60并靠近MA20 | 下一批重点观察 |
| `WATCH_PULLBACK` | RPS双强但回踩MA20较深，仍在MA60上方 | 等企稳，不提前猜底 |
| `WATCH_EXTENDED` | RPS双强但波动或MA60乖离过大 | 等均线靠拢 |
| `NO_BUY_BROKEN_TREND` | 跌破MA60，高RPS可能只是滞后值 | 剔除当日买入观察 |
| `DATA_STALE` / `DATA_GAP` | 行情过期或缺失 | 不参与排序 |

规则只负责统一筛选口径。仓位、流动性、停牌、财报风险和个人风险预算仍需在执行前单独检查。

## 5. 日常运行

运行当前默认批次：

```powershell
python scripts/research/run_screenshot_research.py
```

用新交易日刷新同一批次，不修改原始截图池：

```powershell
python scripts/research/run_screenshot_research.py --price-date 2026-07-15
```

运行一个新批次：

```powershell
python scripts/research/run_screenshot_research.py `
  --pool research_inputs\YYYYMMDD_screenshot_pool.json `
  --classification research_inputs\chain_classification_YYYYMMDD.json `
  --price-date YYYY-MM-DD
```

同一批次同一交易日重复运行会更新同一快照；换交易日会追加历史快照并生成新的日期报告；新来源使用新的 `batch_id`，不覆盖旧批次。

## 6. 新材料处理清单

1. 原始材料按日期归档，并记录来源渠道、作者、发布时间和可访问链接。
2. 提取代码后先按证券名称校验，代码冲突必须写入 `normalization_notes`。
3. 去重后完成主产业位置；跨链业务只放在次位置。
4. 把观点、事实和催化剂分开，不让卖方预测冒充已实现业绩。
5. 更新本地日线后重算全市场RPS50/120/250，保存价格实际日期。
6. 先看简报决定是否存在可执行项，再在详细报告中补财报和产业证据。
7. 每次财报后更新观点的验证状态和反证条件，不删除当时的历史判断。

## 7. 当前限制

- 沪深A股RPS来自本地通达信全市场横截面。
- ETF和北交所当前只计算自身均线趋势，不生成沪深A股横截面RPS。
- 港股本地行情源过期时只保留行业与产业链位置，明确标为数据缺口。
- 截图OCR与名称校正仍需人工复核；黄色、青色等显示颜色不作为信号。
