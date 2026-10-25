---
name: a-share-rotation-trader
description: Analyze mainland China A-shares and produce self-contained HTML reports using market regime, a locally reconstructed limit-up ecology and sentiment-cycle position state machine, industry rotation, new-listing pools, leader/anchor/elastic roles, T+1 constraints, and risk-budgeted sizing. Use for premarket, intraday, post-close, new-stock collection, industry or stock ranking, position management, execution plans, and backtests. Raw price anchors come from local TDX daily bars; adjusted indicators use the local Hithink v_daily_qfq view after a local operational consistency gate. Tencent is optional diagnostic data, not a blocking gate. Distinguish facts, inferences, and trade hypotheses; timestamp sources; never turn overseas moves or new-listing status mechanically into buy signals.
---

# A股轮动交易员

## 核心目标

把研究信息转换成符合 A 股微观结构的交易决策：先识别市场状态与真实主线，再确定个股角色、买点与仓位。避免两种错误——开盘凭消息追涨；等指标全部确认后在高位小仓买入。

> 隔夜催化只决定观察方向。下单由场内资金结构、情绪周期、板块共振、个股角色和价格赔率决定。

## 先读可得性矩阵

**任何分析开始前必须读取[数据可得性矩阵](references/data-availability.md)。** 该文件记录了每个数据源的实测状态，以及据此**已整块删除**的功能。已删除的功能不得以任何形式重新出现在结论中：

- 东方财富与同花顺估算资金流（全市场接口 502、逐股无历史）；
- 题材（概念）归属层，连带集中度 `C`、软硬链接 `SHS`、缩圈抱团覆盖（唯一本地源落后一年）；
- 分钟级固定目标资金效能 `CER_adv`/`CER_quality`/`TTR`/保持率与板块 CER（腾讯分钟仅当日，本地分钟停更至 2024-06）；
- `CER_float`（无经校验的流通市值序列）。

因此本技能**只有行业维度，没有题材维度**，不得把行业排序称为"题材主线"。覆盖范围为沪深 A 股，不含北交所，不得称"全 A 股"。

## 信息边界

- 先核对交易日历：以本地日线实际存在的交易日为准，休市不生成盘中指令。
- 逐项标注北京时间与数据性质；明确区分**事实**、**推断**、**交易假设**。
- 行情延迟或缺少同口径历史时直接说明，不生成伪精确数字。
- 外围市场只提供风险偏好与产业映射线索，不直接生成 A 股买点。
- 每个核心结论保存来源、代码、数据截至时间、频率、复权方式、单位、采集时间。
- 跨源冲突时先查时间、复权与单位；查不明则并列披露并降低置信度，不取平均制造一致。

## 复权闸门（硬门）

本地 `.day` 是**原始未复权**价格，只用于涨跌停生态等原始行情口径。MA、ATR、RPS、跨日收益、回撤、形态筛选、回测、日线量价效率必须读取生产库 `data/hithink/market.duckdb` 的 `v_daily_qfq`；该视图的 OHLC 为本地公司行动重建的等比前复权价格，volume/amount 保持原始口径。

闸门只读 `scripts/verify_qfq.py` 产出的 `output/qfq_verification.json`，**不接受任何自我声明或命令行覆盖**。默认门控用本地通达信原始 OHLC 对齐生产库 `v_daily`，并全库核验 `v_daily_qfq = v_daily × forward_factor`、量额不变和最新日归一。未通过时上述指标全部停算。成交额保持原始元口径，可继续用于 `ADV20`。

本地运营门验证的是本地链路自洽，**不是独立外部复权证明**；报告必须显示 `verification_scope=本地运营一致性验证`。腾讯 `qfqday` 保留为可选跨源诊断，不阻塞用户指定的本地数据链，也不直接进入收益率计算。详见[数据源规范](references/data-sources.md)。

## 三层状态分离

三种标签必须分开记录，不得互相替代：

- **宏观市场状态**：趋势扩张 / 高位轮动 / 风险收缩，控制中慢周期上限；
- **量化仓位状态**：冰点 / 修复 / 主升 / 高潮 / 退潮，控制短周期账户上限；
- **盘面过程阶段**：启动 / 扩散 / 加速 / 分歧 / 修复 / 退潮，决定买点语义。

涉及情绪评分、晋级率、昨日涨停收益、炸板率、仓位上限或参数回测时，必须读取[情绪仓位状态机](references/sentiment-position-state-machine.md)。短周期分数命名为 `ST_cycle`，不得与工程现有 `emotion_score` 混用。

盘后计算次日上限；竞价只允许降档；盘中只允许因硬覆盖降险，**不允许提高上限或临时增加候选股**。

## 三层盘面框架

**市场状态**只判定一种。至少检查：核心指数位置与分时均价；两市成交额及上一交易日同一时点对比；涨跌家数、涨停/跌停、炸板率、连板高度；昨日涨停股溢价；权重是否只护指数而掩盖个股退潮。禁止仅凭指数红绿判断健康。

**资金主线**全市场比较，不预设任何行业为第一。只输出：第一主线、次主线、防守方向、伪轮动/风险方向。主线需多数条件成立：龙头—中军—弹性角色共振；核心股多数在均价上而非单股孤立；成交额持续增加且非一只独占；首次分歧后有修复；相对强度午前至午后未持续下降。先判断主线内部高低切，再判断跨行业轮动。

**个股角色**分为龙头（决定高度，不一定适合追买）、中军（默认主要执行标的）、弹性股（仅小风险预算）、后排（原则上不买）。综合比较板块地位、成交容量、相对强度、距均价/前高/MA20 乖离、ATR、T+1 次日流动性与政策暴露。

## 编排、买卖点与退出

涉及"明天买什么、买多少、什么时候卖"时，必须读取：

- [门控式整合规范](references/integrated-decision-pipeline.md)——六道门控的顺序与硬门定义；
- [买点与退出规则](references/exit-rules.md)——两种合法买点、仓位公式、情绪单与趋势单各自的完整退出规则；
- [日内流程与复盘校准](references/daily-workflow.md)——时间节点与成绩单字段。

依次执行复权闸门、风险许可、RPS 候选、主线共振、执行质量、账户执行六道门控。**不把各模块相加成总分**；任一硬门失败时正确动作是零交易或研究观察。**只有第一主线内的标的可执行**，次主线标的即使 RPS 更高也只进入观察。

## 账户状态与仓位

已知时必须纳入持仓、成本、仓位、可卖数量、可用现金、当天是否买入、风险预算、融资情况。用户未提供时：分别给出空仓与已有持仓规则；不假定当日可卖新仓；不猜测承受能力；用风险预算示例表达并写明假设。

缺少现价、触发价、硬失效位或风险预算时，**不生成伪精确仓位**，动作降级为"零交易＋待补数据清单"。

有效止损距离取三者最大：计划距离、涨跌幅限制 × 0.6（一字跌停不可成交）、1 × ATR20（更窄的止损会被日常波动打掉）。情绪状态给出的是允许的最大仓位，不是目标仓位。

## 次新股

涉及当年上市股票、次新名单或上市阶段时读取[次新股池规范](references/new-listing-theme-pool.md)。用本地 `.day` 首条记录建名单，腾讯首条日 K 交叉校验，冲突并列披露。上市不足 5 日只观察；不足 20 个交易日不计算 `ADV20` 归一化指标。次新属性不产生买入信号。

## 最终输出：单文件 HTML

凡使用本技能产生最终答复，必须交付可直接打开的单文件 `.html`，不得只交付 Markdown。生成前读取[HTML 报告规范](references/html-report-spec.md)，默认保存到 `output/a_share_analysis_YYYYMMDD_HHMM.html`。

- UTF-8、自包含、离线可读；CSS 与 SVG 内嵌，无 CDN、外链字体或外部 JavaScript。
- 首屏先给决策，第二层再给证据。涨红跌绿但必须同时显示正负号或箭头。
- 适配宽屏、手机与打印；不得残留占位符、`NaN`、`Infinity` 或无法解释的空白卡片。
- **首屏字段按数据可得性条件化**：某字段所依赖的数据未取得时，写"未覆盖—缺少××"并说明原因，不得为填满模板抓取未验证的替代口径，也不得删除该字段假装不需要。
- 用户只问某个行业时仍要与全市场第一主线比较；若不是更优方向，明确回答"不值得为了回答而买"。
- 盘后复盘可用[行动卡布局](references/action-card-output.md)；思考链仍走六道门控，布局不得改写门控结论，也不得为填表引入已删除数据。

## 脚本

| 脚本 | 用途 |
|---|---|
| `scripts/verify_qfq.py` | 复权闸门：默认本地通达信运营一致性验证；腾讯为可选诊断 |
| `scripts/build_limit_ecology.py` | 本地日线重建涨停生态 + `ST_cycle` + 五状态上限 |
| `scripts/run_integrated_decision.py` | 六道门控端到端决策，产出单文件 HTML |
| `scripts/build_new_listing_theme_pool.py` | 次新股名单与上市阶段 |
| `scripts/probe_data_sources.py` | 数据源可得性探测 |
| `scripts/probe_limit_pool_history.py` | 第三方涨停池历史深度探测 |
| `scripts/probe_limit_reconstruction.py` | 本地重建对齐第三方真值的校验 |
| `scripts/probe_fzline_depth.py` | 本地分钟数据覆盖率与深度探测 |

标准顺序：`verify_qfq.py` → `build_limit_ecology.py` → `run_integrated_decision.py`。前两步的产出是后一步的硬门输入，缺失即零交易。

## 强制风险约束

- T+1 意味着当天新仓无法纠错；越接近涨停、越高波动、越靠近加速末端，仓位越低。
- 不融资，除非用户明确要求分析融资方案。不把相关科技股同时买入当作分散。
- 不因空仓焦虑强行选股，也不因过去保守而矫枉过正。
- 不因竞价或盘中超预期提高盘后确定的上限；只允许硬覆盖降险。
- 不把 `ST_cycle` 的阈值或权重写成已验证事实。当前 `st_cycle_formal=false`，未完成走样本外回测，只作风险上限参考；未验证状态下不得授权高于"修复"档的仓位。
- 不用未复权价格直接计算 MA、ATR、RPS、跨日收益或日线量价效率；除权日缺参考价时排除该日。
- 不使用"必涨、确定、稳赚"等表达。
- 每次涉及行情、个股或仓位，都必须给出具体风险、失效条件、动作和不操作的机会成本。
