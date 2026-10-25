# 产业链瓶颈数据库与分析框架

本模块把产业链研究从散文式笔记变成结构化、可更新、可评分的数据，并与现有 RPS 候选池联动。它用于研究排序，不构成买卖建议。

## 1. 双层框架

第一层回答“选什么”：从终端需求向上游回溯，寻找供给难扩、客户难换、认证周期长、具有议价权的瓶颈环节及其所有者。

第二层回答“什么时候值得研究”：把每日 RPS 强度、候选池共振和股价过热程度叠加到产业评分上。技术信号不能替代产业证据，产业映射也不能替代买点。

## 2. 数据模型

| 数据表 | 作用 |
|---|---|
| `themes` | AI 光互连、人形机器人等产业主题 |
| `nodes` | 材料、器件、模块、系统和终端需求等产业环节 |
| `node_relations` | 上游供应、使能、集成和支撑关系 |
| `companies` | A 股、美股及其他市场的统一证券主表 |
| `company_exposures` | 公司到产业环节的映射、评分和证伪条件 |
| `evidence` | 公司披露、专利、客户认证、行业数据等证据 |
| `viewpoints` | 卖方、大V与内部判断；记录立场、采纳状态、验证及反证条件 |
| `events` | 催化剂、风险、概率、影响和反向验证信号 |
| `signal_observations` | 每日 RPS 候选池快照 |
| `research_queue` | 尚未完成产业映射的强势候选股 |
| `research_batches` | 截图、聊天名单等一次研究任务的批次主表 |
| `research_batch_members` | 批次证券、来源次数、主行业和主产业位置 |
| `research_batch_placements` | 证券在产业链中的主位置与次位置 |
| `trend_snapshots` | 按批次和交易日保存价格、RPS、均线位置与决策状态 |

可维护的原始数据位于 `industry_chain/seed_data.json`，SQLite 文件由它生成到 `data/industry_chain.db`。

## 3. 评分口径

产业环节分数由供给集中度、替代难度、扩产弹性、扩产周期、认证周期、定价权和需求确定性组成。

公司卡位分数由业务纯度、技术壁垒、产能稀缺、定价权、客户黏性、瓶颈所有权、映射置信度和证据质量组成。

联动总分：

```text
瓶颈总分 = 42% × 环节分 + 58% × 公司卡位分
技术信号分 = 82% × RPS 加权分 + 18% × 多层共振分
联动总分 = 62% × 瓶颈总分 + 38% × 技术信号分 - 过热惩罚
```

过热惩罚只用于降低追高优先级，当前依据 `ret20 > 30%` 和 `vs_ma60 > 35%` 递增，最高扣 20 分。

## 4. 常用命令

初始化或用种子数据更新数据库：

```powershell
python scripts/screening/run_industry_chain.py init
```

查看产业主题和 AI 光互连图谱：

```powershell
python scripts/screening/run_industry_chain.py themes
python scripts/screening/run_industry_chain.py map --theme AI_OPTICAL
```

查看瓶颈所有者排名：

```powershell
python scripts/screening/run_industry_chain.py rank --min-confidence 60
python scripts/screening/run_industry_chain.py views --entity-type security --entity-code 601869.SH
python scripts/screening/run_industry_chain.py rank --theme HUMANOID
```

与最新 Super 池联动并输出完整结果：

```powershell
python scripts/screening/run_industry_chain.py rps --pool super --write output\industry_chain_latest_super.csv
python scripts/screening/run_industry_chain.py rps --date 20260709 --pool l2
```

查看尚未建档的强势候选：

```powershell
python scripts/screening/run_industry_chain.py queue
```

截图证券池的完整流程、输入格式和状态定义见 [截图池产业链与RPS研究流程](screenshot_research_workflow.md)。

## 5. 每日研究流程

1. 运行原有每日选股流程，生成 L1、L2、Super、火车轨和早期启涨候选池。
2. 运行 `rps` 联动分析，已映射标的进入双层评分，未映射标的自动进入研究队列。
3. 优先研究队列中重复出现、RPS 较高的标的，先确认公司名称和主营业务，再建立产业环节映射。
4. 至少补充一条公司一手证据，并填写业务纯度、客户认证、有效产能和证伪条件。
5. 只有“产业证据 + 技术信号 + 不过热”同时满足时，才进入重点核验层。

## 6. 数据维护规则

- 所有事实必须带 `as_of_date`，过期信息不能默认继续有效。
- 概念名单、社交媒体和二手文章只能作为线索，不能给高 `evidence_quality`。
- 公告扩产必须沿“设备、工艺、良率、认证、稳定交付”逐项核验。
- 公司可以映射多个环节，联动分析暂取瓶颈分最高的映射。
- 每条公司映射必须填写 `disconfirming_conditions`，避免只记录看多理由。
- 种子数据是方法论案例，不是实时事实库；正式决策前应复核公司公告和原始资料。
