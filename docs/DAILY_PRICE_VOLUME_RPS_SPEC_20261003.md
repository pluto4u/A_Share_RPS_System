# 本地日K量价与RPS右侧趋势研究闭环

日期：2026-10-03。
状态：本地规格草案；未发布Issue，未应用ready-for-agent标签；本次不执行新回测或修改冻结策略。

## Problem Statement

用户希望使用本机通达信日K，找到合理、可解释的量价关系与K线走势，
在当日收盘能够确认股票右侧趋势开始和结束，并检验是否能获取扣成本后的收益。
用户只操作日线，能够在确认后的下一交易日开盘处理；研究资金100万元，
组合最大回撤20%是用户要求的验收上限，不是系统能够保证的损失上限。

用户在设计访谈第一轮明确：以上班族的中期波段收益为目标；仅通过日K量价寻找走势规律，
市场环境必须影响组合敞口，退潮时应轻仓；收益目标年化50%。
第二轮补充：退潮期间最多总资金20%敞口；预警与失效分开研究；熊市以不亏为目标。
第三轮补充：退潮优先缩减结构较弱的持仓，熊市允许完全空仓，50%仅为非熊市阶段目标。
退潮/熊市的操作性定义、缩减存量敞口的路径、年化计算口径及50%目标的阶段归属仍需明确，不能据此宣称已具备获利方案。
环境敞口研究将另行登记，原冻结A/B组合不直接接入未验证的门控。

当前问题并非缺少筛选条件，而是研究尚未形成可信闭环：

- 简化趋势池候选过多，用户无法人工筛查；继续叠加复杂条件又可能错过有价值的形态。
- 原四段式与趋势持续规则没有覆盖所有深回撤修复和二次启动，不能将漏选全部归因于RPS。
- 现有收益尚不足以支持实盘适用性判断；退出过早、结构真正失效、成本和持仓占位的影响需要分别验证。
- 高长期RPS可以与大幅回落同时发生；短期RPS也不一定先于价格恢复。
- 用户提供的已知走势只能帮助提出假设，不能证明当时能识别牛股、能赚钱或找到最优参数。
- 2022年及之后的数据已经被查看，不能再把它包装成未见过的独立样本外验证。

本规格将“找到右侧起止”限定为因果、可复现的研究信号：
不要求事后精确捕捉最低点或最高点，不将未来谷底、未来收益写入当天判断。

## Solution

建立一个本地、离线、日线级的研究闭环：既有通达信缓存输入，
产生量价阶段解释与冻结规则信号，经同一执行模型形成逐笔模拟和20只组合账本，
最后输出有证据边界的研究结论及供人工复核的候选状态。

核心分工如下：

1. 量价与K线结构承担趋势阶段及失效的解释，不能用高RPS覆盖价格结构破坏。
2. RPS描述近期、中期、长期相对表现及阶段变化；增量价值需要独立检验，不能预设为有效门槛。
3. 保留原冻结A/B入场及共同执行。下一轮优先只改变退出假设，分离退出影响与新增入场影响。
4. 首次启动、深回撤修复、强势整理后二次启动先作为研究背景分类；新增入场形态必须另行登记，不能冒充原四段式。
5. 结果允许失败。没有净收益提升、超额证据不足或缺少独立验证时，明确说明，不自动继续调参直到出现好结果。

交付是研究工具、复现证据和人工复核材料，不是自动荐股、自动下单或收益保证。

## User Stories

1. As a daily A-share researcher, I want to use my local Tongdaxin daily bars, so that I do not need to export or download quotes again.
2. As a researcher, I want to reuse the existing price and RPS caches, so that all comparisons share the same data version.
3. As a researcher, I want missing files and malformed data to produce explicit errors, so that failures cannot silently change the sample.
4. As a researcher, I want missing observations to remain missing, so that invented prices or RPS values cannot influence decisions.
5. As a researcher, I want stock identities and screenshot conflicts to be recorded, so that charts cannot be assigned to the wrong company.
6. As a researcher, I want RPS to retain its whole-market denominator, so that candidate-only rankings cannot exaggerate strength.
7. As a daily investor, I want interpretable price-volume phases, so that I can understand why a trend is considered to be starting or failing.
8. As a researcher, I want decline, quiet base, volume expansion and later retest to remain separate events, so that expansion-day entry cannot be mislabeled as retest entry.
9. As a researcher, I want the original trend-continuation hypothesis preserved, so that it can be compared without silently redefining it.
10. As a researcher, I want first launches, deep-correction repairs and second launches distinguished, so that different chart backgrounds are not forced into one explanation.
11. As a researcher, I want the RPS comparison date and interval return available, so that price recovery with low short-period RPS can be explained.
12. As a researcher, I want high RPS and broken price structure shown together, so that relative strength cannot hide trend failure.
13. As a daily investor, I want close-confirmed research signals, so that minute data and intraday monitoring are unnecessary.
14. As a researcher, I want historical fills at the next session under the existing engine, so that returns do not rely on impossible same-close execution.
15. As a researcher, I want limit-up rejection and limit-down exit handling preserved, so that favorable fills are not invented.
16. As a researcher, I want frozen entry offers reused across exit experiments, so that the effect of exits can be isolated.
17. As a researcher, I want portfolio admissions and rejections logged, so that exit-driven changes in cash and occupied slots remain visible.
18. As a researcher, I want no more than 20 simultaneous holdings with the existing RPS120 priority, so that comparisons follow the agreed portfolio policy.
19. As a researcher, I want gross profit, costs and net profit reconciled, so that apparently profitable patterns cannot conceal implementation drag.
20. As a researcher, I want a physical in-sample cutoff, so that future prices cannot influence features, selection or settlement.
21. As a researcher, I want viewed history marked as contaminated, so that it is not presented as independent validation.
22. As a researcher, I want hypotheses, inputs and implementations frozen before calculation, so that post-result changes can be detected.
23. As a researcher, I want original baselines retained under the same execution model, so that improvement has a meaningful reference.
24. As a researcher, I want trade-level and portfolio-level results separated, so that candidate returns are not mistaken for account returns.
25. As a researcher, I want equity, drawdown, annual results and stock contributions reported, so that concentrated or unstable returns are visible.
26. As a researcher, I want next-day and next-week returns defined on market sessions, so that suspension does not shift the evaluation horizon.
27. As a researcher, I want incomplete horizon windows and unfinished trades flagged, so that boundaries cannot create false performance.
28. As a researcher, I want reproducible uncertainty estimates and multiple-testing caveats, so that an exploratory association is not called a proven edge.
29. As a researcher, I want failed hypotheses retained, so that only successful runs are not selectively reported.
30. As a daily investor, I want a compact evidence-backed review list rather than the entire trend pool, so that the output is usable for manual review.
31. As a reviewer, I want every numeric conclusion traceable to an output table or inspected implementation, so that no result depends on estimation or chart pixels.
32. As a reviewer, I want offline tests at the research entry point, so that the full workflow can be checked without brokerage, LLM or network access.
33. As a daily investor, I want the system to distinguish research signals from live instructions, so that actual investment decisions remain mine.
34. As a researcher, I want unresolved data and execution limitations disclosed, so that adjusted-unit research is not mistaken for a ready-to-trade account model.

## Implementation Decisions

### 数据与研究边界

1. 唯一行情来源为本地通达信日K及已经生成的缓存。默认只读已有价格面板和全市场RPS面板；不重新下载、不重建面板，不擅自改换数据源。缓存缺失时显式失败，而非自动启动一次新数据构建。
2. 股票代码是身份键，当前名称只用于显示，不能推断历史ST、停牌原因或历史涨跌停资格。名称冲突、截图顺序冲突及截图与缓存差异单列记录。
3. 进入研究的输入必须校验日期顺序、股票重复、字段维度、OHLC合法性、量额和缺失位置。资格剔除必须记录原因，与解析失败分开；不能静默吞掉异常。
4. RPS50、RPS120、RPS250沿用现有全市场缓存口径，不在六股、候选池、持仓池或行业池内重新排名。缺失保留null，显示为“缺”。独立排名审计必须披露实际核对日期和范围。
5. 支持250日区间收益需信号当时已有251根有效日K；不得以未来积累的历史补足早期资格。股票交易K线与市场交易日历明确区分。
6. 样本内为2016-09-01至2021-12-31，价格/RPS输入在进入研究消费者前物理截断。历史暖机仅使用更早数据；结算、对照及边界估值不能越过截止日。
7. 2022-01-01之后已访问的数据只用于标明污染的历史复盘或用户指定案例解释，不能进入下一轮选择。不得重新运行原一次性历史复盘、改名为独立样本外或读取其结果挑参数。真正独立验证需另行登记未来未见数据段。
8. 每个实验计算前保存假设、配置、输入与代码指纹、访问范围和时间戳。原冻结版本及产物保持只读；改变输入或实现需要新版本，不能覆盖旧结果。禁止把仅有执行日志当成已证明绝无泄漏。

### 冻结入场与因果阶段

9. A保留下跌→横盘缩量→放量阳线收盘站上MA20→放量后缩量回踩确认的顺序。下跌收盘高点在前半段、低点在后半段；后续横盘振幅相对下跌段变窄，成交量低于下跌段；回踩从放量之后开始，冻结确认时已知的回踩最低价。禁止放量当天作为A回踩进入。
10. A沿用原冻结组合1276：下跌长度60、幅度20%、横盘40、振幅上限12%、缩量比例0.8、放量倍数2.0、回踩窗口3、MA20上方容差3%。这些是已有样本内冻结记录，不是本规格提出的新参数，也不是盈利推荐。
11. 原A允许网格仅作为合规审计边界保留：下跌长度20/40/60，幅度10%/15%/20%，横盘10/20/40，振幅8%/12%/15%，缩量0.6/0.8，放量1.5/2.0，回踩3/5，MA20上方3%/5%。本轮不重新搜索该网格，不添加新轴。
12. B沿用已有单一趋势持续假设：MA20较5根前上升、近20根最低价较其前20根抬高；确认前3根包含实际回落、量低于此前20根均量，并在各自MA20至其上5%以内；确认日为上涨阳线且仍在同一均线附近。沿用确认前整理低点，不伪造放量日，也不新增大跌前提、新高突破或RPS筛选。
13. 首次启动、深回撤修复、强势整理后二次启动为研究背景，不是产业评分受控词表、数据库枚举或已验证交易模式。新增形态定义应独立登记为样本内假设，不替换A/B或为使六张赢家图全部入选而同时修改规则。
14. RPS的角色先作为可解释特征：阶段端点、百分位点变化、比较基点及价格结构分歧。不得机械要求RPS50先于价格修复，不引入新的数值门槛。任何过滤、排序或退出增量作用都要单因素检验，不能从分组均值直接宣称新组合盈利。
15. 截至确认日可知特征和事后路径必须分列。事后谷底、段内最低RPS、后续最高价格仅用于解释，并明确标记使用后续数据；不进入当天信号、入场资格或止损边界。

### 趋势结束与退出实验

16. 原A/B失效基线保持收盘跌破MA20或跌破该次冻结回踩/整理低点，随后由原成交模型模拟退出。高RPS不能自动取消结构失效，不能因为之后反弹就声称原退出当时错误。
17. 下一轮优先开展退出单因素研究：固定原候选入场、RPS排序、费用、成交和组合政策，仅允许明确登记的退出触发假设变化。现有引擎包含的其他退出能力不等于本轮已经选定或授权搜索其具体参数；具体候选及选择准则必须在读取本轮样本内结果前登记。
18. 同一候选事件的退出影响与真实组合影响分开计算。退出变化会影响占位、现金、后续组合接纳，因此冻结的是候选入场条件，不是要求每个组合版本的实际成交数、成交集合完全相同。必须逐条记录接受/拒绝及原因。
19. 是否延后结构确认、量价失效能否减少过早退出、RPS分歧是否有增量解释力，都只能作为待检验问题；本规格不据2026案例指定新退出周期、阈值或最优止损。

### 执行、账本与对照

20. 只做日线收盘确认、下一市场交易日开盘的历史模拟；开盘涨停不进入。停牌缺交易、涨跌停判定、无法退出时的顺延和费用沿用原引擎，不实现第二套有利成交模型。收盘触发不等于收盘已经成交。
21. A/B共享最多20只和同一现金账本；同日RPS120降序、代码稳定排序，原A/B并列优先顺序及原资金分配政策不变。不能为了提高收益强行满仓、放大杠杆或将复权研究单位说成真实整手股数。
22. 保留原A、B、联合组合、A放量当天进入对照及基础控制。基础控制为当日站上MA20且20日平均成交额不低于1亿元，资格与执行沿用原规范。对照的原退出定义也保持明确，不把同一引擎误称为所有对照拥有同一信号和退出条件。
23. 逐笔记录信号、模拟进入/退出日期与价格、退出触发原因、RPS快照、冻结结构边界、未完成标记和费用。逐日账本记录现金、持仓、市值、权益及拒绝；股票贡献必须与组合权益变化守恒。
24. 截止日仍持有的头寸只作扣原退出费用的研究边界估值并显式标记，不生成现实卖出结论。净值末日也须包含该估值费用，各对照一致。
25. 隔日为信号后第1个市场交易日收盘相对信号收盘；隔周为第5个市场交易日同口径。停牌导致目标市场日缺收盘时写缺，不能改取复牌后第N根，也不能填零制造同向率。

### 研究输出与结论约束

26. 输出量价阶段证据、RPS背景、信号状态、形态失效参考及可核查原因；工作清单只呈现规则支持的人工复核候选。若数量仍过多应报告不可用，不私自追加未经登记的筛选或随意截取赢家。缺少行业元数据时标缺，不推测行业。
27. 报告分开呈现候选事件、单股非重叠交易、组合实际进入、组合净值和拒绝事件；报告扣成本净收益、组合年化/最大回撤、年度稳定性、持仓与现金暴露、股票贡献及集中度。个股价格回撤、20日最大回撤和组合最大回撤不能混用名称。
28. 沿用既有月度区块2000次、种子7的研究不确定性方法；保留其探索性和多重比较局限。区间跨零或整体不高于零时不能宣称明确超额优势；不能把已经看过的历史区间包装成独立证据。
29. 原样本内选参资格至少300笔及相对基础控制平均超额排序保持留痕，不用实际组合成交数偷换资格笔数，也不重新挑选原A。新实验报告各层样本量，不通过时不降低已有门槛。
30. 收益改善必须来自扣成本实际组合与明确对照，不能只报告六股命中、毛收益、最佳个股或某个候选分组。未改善写“没有提升”，信息不足写“证据不足”，真实独立验证缺失写“尚未独立验证”。用户提出非熊市阶段年化50%、熊市不亏目标，允许熊市空仓；阶段的因果标记、年化口径与硬性验收属性仍待确认。全周期表现必须同时披露，不得事后删除亏损阶段、自降目标、选取有利年度或更换分母后宣称收益够用。
31. 所有数值引用计算表或已检查实现，记录来源和as_of；不按截图像素估数。报告分离算术事实、描述性关联和下一轮假设。研究模拟不是实时指令，verdict不得包含可执行买卖结论，不连接券商或写入产业证据数据库。

## Testing Decisions

### 测试接缝

主要验收接缝为“离线研究入口→冻结清单、事件/模拟成交、20只组合账本、报告”。
优先扩展已有入口级缓存夹具及产物行为测试，不为特征、退出、账本、报告分别新造多套内部测试接口。
现有纯函数测试继续作为边界回归，但不以测试内部实现细节代替端到端验收。

已经向用户核对这一接缝。用户补充的是业务验收目标：使用本地通达信K线，
找到合理量价/K线关系，确认右侧趋势开始与结束，从而获取收益。
本规格据此优先验收可复现信号与扣成本组合证据，不把该答复说成已逐项确认技术接口。

### 测试原则与既有先例

- 好的测试验证外部行为、日期、实际产物、失败信息和账本守恒，不锁定循环写法、私有方法或数据结构细节。
- 用小型合成缓存运行完整离线入口，保留合法OHLCV、全市场RPS字段及市场日历。夹具的人工数值只用于测试，不混入真实结果报告。
- 复用现有双引擎测试中的入口产物、物理截断、放量后回踩、涨停拒绝、组合资金守恒和末日费用先例。
- 复用现有诊断测试中的市场日历缺失、费用分解和退出路径核对先例。
- 复用现有RPS轨迹及案例测试中的未来扰动、缓存百分位保留、缺失不填零、高长期RPS与破MA20共存和事后谷底标记先例。
- 全部测试离线，无真实LLM、行情服务或交易接口。定向检查和全仓检查结果分开报告，不把此前局部通过写成全仓通过。

### 功能验收

1. 缺缓存、重复代码、日期乱序、OHLC/字段异常必须产生明确错误；有资格排除记录，不能静默跳过。
2. 截断后所有消费者、对照和结算都不获得2022年后价格。扰动未来价格/RPS不能改变已截止的特征、信号和输出。
3. 原A/B入场事件与冻结版一致；A确认日严格晚于放量日，冻结低点不受后续低点修改影响。
4. 趋势结束触发只依赖截至当天可知数据；模拟成交严格晚于收盘确认，涨跌停和停牌行为符合原执行模型。
5. 参数/输入/实现与冻结清单不一致时拒绝冒充原版结果；历史结果访问不能进入挑选函数。
6. RPS既有数值不变，全市场口径不变；RPS变化按百分位点报告，缺失与零分开。
7. 仅修改退出的实验保持原候选集合及排序输入不变；组合实际接纳变化必须由账本和拒绝原因解释。
8. 最多20只、共享现金和同日优先级正确；从未被接受的候选不得占据组合仓位。
9. 毛盈亏减成本等于净盈亏；现金加市值等于权益；股票贡献与总权益变化一致。
10. 末日未完成头寸明确标记边界估值，末日曲线含一致费用，不能称为真实退出。
11. 第1/5市场日收益日期正确，缺目标日收盘或越界时标缺；不得改用股票第1/5根来规避停牌。
12. 空候选、空成交和无合格假设都生成稳定表头及明确说明，不伪造有收益结果。
13. 报告能从摘要追溯到逐笔与曲线，清楚区分候选、单股链及账户收益，且包含失败实验。
14. 截图映射冲突及精确日期缺失保留；事后选中的六股不作为独立验证集或入场优化目标。

### 研究成功标准

- 工程通过：因果、离线、可复现、数据与执行一致、守恒、输出可追溯；规则失败也应能完成一个合格的研究交付。
- 研究改善：扣成本净表现为正，优于明确且同执行模型的对照；超额区间证据与年度稳定性、占位及集中度同时报告；组合最大回撤不超过用户20%上限。
- 原A回踩和放量当天进入的比較保留；退出实验另以冻结退出为主对照。不能只超过某个弱对照就宣称整体方案有效。
- 即使上述研究条件通过，缺少真正独立验证、明确的年化目标验收口径或真实账户适配时，也不能宣称已满足实盘赚钱目标。
- 未通过必须保留结果并给出“没有提升/证据不足/尚未独立验证”，不自动生成基于已看历史的新具体参数。

## Out of Scope

- 自动交易、券商连接、盘中或分钟指标、实时可执行买卖计划及保证收益。
- 创新高买入、海龟55日突破，以及把A改成放量当天进入。
- 重下载行情、重建面板、重搜原入场网格或另找一套外部参数。
- 本轮同时修改入场、退出、RPS门槛、排序、资金政策或费用模型。
- 根据2022年后表现、2026六张赢家图或最终牛股名单挑选参数。
- 将首次启动、深回撤修复和二次启动直接合并成新策略；这些新增形态的可执行定义与验证需独立登记。
- 修改产业证据库、六维评分词表、DB真相源或从视图写回数据库。
- 自动补全历史ST、退市股、行业、流通股本或停牌原因；缺数据保持缺。
- 本轮完成真实整手成交、原始未复权限价、最低佣金、冲击与容量等账户适配；现有研究不能冒充已解决这些问题。
- 在未指定跟踪器、项目及标签配置时擅自创建Issue、标签、仓库或PR。

## Further Notes

### 现有基础与未完成目标

已有双引擎冻结规范、样本内诊断、RPS阶段轨迹和六股案例核对，
均为本规格的基线；它们不等于已经完成退出改进或找到可盈利策略。
本次仅合成规格，不执行新行情计算或新回测。

六图窗口按用户“大致2026年6月至9月底”操作性定义为2026-06-01至2026-09-30，
不能声称截图精确起止日期已经确认。
本地名称为源杰科技，用户此前写源捷科技；图5/6与义翘神州/百普赛斯的原名单顺序存在冲突，
尚待确认。截图副图颜色不能直接当成已知RPS周期图例。

### 待登记而非已决定的内容

- 下一轮具体退出候选、实验数量、选择准则及分年稳定性判断，须在计算前登记；本规格不提供新具体参数。
- 新形态规则若需另立研究，要先明确因果定义，不承诺覆盖全部用户案例。
- 用户非熊市阶段年化50%、退潮最多20%总敞口且优先缩减弱结构、熊市不亏且允许空仓、预警/失效分开研究已经明确；阶段因果定义与年化口径、退潮/熊市证据、弱结构排序及执行路径、真正独立验证区间、真实交易成本及容量信息仍未确定。
- 这些未决项不能通过读取已污染历史补全，也不能被默认成最有利假设。

### AGENTS要求及发布状态

适用禁止臆造数字、确定性核对、版本可追溯、密钥安全和人工把关要求。
行业claim的证据约束继续有效，但本规格不创建此类记录。
本规格中的历史进入/退出只指模拟，不能变成实时可执行结论。
根AGENTS要求完成任务后开PR并通过pytest、ruff、mypy；
当前工程不是Git仓库，未配置远程与目标项目，不能履行PR创建。
不得为绕过这一限制自行初始化仓库或假称已提交。

to-spec要求发布至项目Issue跟踪器并标记ready-for-agent。
当前未提供目标跟踪器、项目关联和triage标签配置，文档检索也未发现这些配置。
因此仅保存本地草案，未发布、未打标签；请先运行/setup-matt-pocock-skills。
配置明确后应发布此规格而不是重新采访业务需求，并核实标签实际应用成功。
