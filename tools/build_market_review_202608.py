"""
================================================================================
A股 2026年7月-8月首周 走势复盘 + 修复路径推演  报告生成器
================================================================================
生成单文件 HTML（纯内联 SVG，零依赖，断网可开），风格与 macro/dashboard.py 一致。

数据全部来自公开报道，来源见报告末尾「数据来源」章节。
推算值（8/5、8/6 上证收盘点位）已在报告中明确标注。

用法:
    python tools/build_market_review_202608.py
输出:
    output/market_review_202608.html
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

OUT = Path("output/market_review_202608.html")

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',"
        "'PingFang SC',sans-serif")

UP, DOWN, NEU = "#dc2626", "#16a34a", "#94a3b8"   # A股习惯：红涨绿跌


def esc(s):
    return html.escape(str(s))


# =============================================================================
# 数据
# =============================================================================

# 7月主要指数月度涨跌幅（%）
JULY_INDEX = [
    ("上证综指", -6.4, "3832.26 · 回吐年内全部涨幅"),
    ("深证成指", -15.0, "跌幅超 15%"),
    ("中证500", -15.0, "跌幅超 15%"),
    ("创业板指", -23.0, "历史第二大单月跌幅"),
    ("科创50", -25.9, "月内曾创 2255.25 历史高位"),
    ("上证50", -1.5, "相对抗跌（估算，权重价值股护盘）"),
]

# 7月板块涨跌（%）— 近20个交易日口径
JULY_SECTOR_UP = [
    ("煤炭", 14.29), ("油气开采及服务", 13.54), ("白酒", 11.60),
    ("银行", 11.19), ("教育", 10.29), ("保险", 6.37),
]
JULY_SECTOR_DOWN = [
    ("半导体", -36.45), ("存储芯片", -31.89), ("元件", -31.16),
    ("CPO概念", -30.36),
]

# 8月首周 A股日线
AUG_WEEK = [
    # 日期, 上证收盘, 上证涨跌%, 创业板涨跌%, 科创50涨跌%, 成交额(万亿), 备注
    ("8/3 一", 3809.66, -0.59, -1.24, None, None, "半导体主力净流出 200.6 亿，资金转向电力/核电/电网/光伏"),
    ("8/4 二", 3822.28, +0.33, +5.64, +4.09, 2.21, "通信设备主力净流入 221 亿；元件、半导体各超 100 亿"),
    ("8/5 三", 3878.47, +1.47, +1.32, +4.78, 2.65, "放量普涨，半导体/AI/算力/空天领涨（收盘点位为推算）"),
    ("8/6 四", 3900.26, +0.56, None, None, None, "震荡整理（涨幅与点位为推算）"),
    ("8/7 五", 3940.04, +1.02, +1.35, +2.51, 2.66, "CRO 单日涨超 10%，PCB 涨停潮；赚钱效应仅 51%"),
]

# 海外市场
OVERSEAS = [
    ("美国 · 标普500", "7710.02", "8/6 -0.18%", "8/4 曾创 6 月以来新高 7736.52", NEU),
    ("美国 · 纳斯达克", "26348.35", "8/6 -0.06%", "8/4 +2.59% 后连续两日回落，存储芯片重挫", DOWN),
    ("美国 · 道琼斯", "53885.10", "8/6 -0.85%", "8/5 创历史新高 54349.12 后终结五连涨", DOWN),
    ("日本 · 日经225", "约 64000-66000", "7/17 -4.0%", "7/17 单日 -2694 点跌破 65000，AI/半导体信用抛售", DOWN),
    ("韩国 · KOSPI", "6257.45", "8/3 -5.12%", "三星电子 -7.52%、SK海力士 -6.90%", DOWN),
    ("台湾 · 加权指数", "44611.60", "8/5 +2.9%", "8/4 台积电跌 50 元拖累；8/3 外资重回卖超", UP),
]

# 中美对抗事件时间线
GEO_TIMELINE = [
    ("2026-05-15", "双", "缓和", 4, "特朗普访华，北京峰会",
     "原则同意贸易理事会项下讨论同等规模产品对等降税，规模各 300 亿美元或更多"),
    ("2026-06-08", "美", "升级", 4, "国防部 1260H「中国军事企业」名单扩容",
     "阿里、比亚迪、宇树科技、百度、京东方、<b>药明康德</b>、蔚来、中创新航、亿纬锂能列入。"
     "突破军工界限，涉及 AI、新能源车、机器人、生物医药民用企业"),
    ("2026-06-22", "中", "升级", 3, "10 家美企列入出口管制管控名单",
     "美国稀土公司、杰亚机器人、蒂尔无人机、鲍尔航空航天、芒廷帕斯材料等。"
     "同日财政部：政府采购不得采购 46 家美企产品"),
    ("2026-06-24", "中", "升级", 2, "《产业链供应链安全调查工作办法》",
     "为后续系统性反制建立制度化工具"),
    ("2026-07-09", "美", "升级", 3, "232 条款调查：商用飞机与喷气发动机",
     "认定进口相关产品可能损害国家安全"),
    ("2026-07-23", "美", "升级", 4, "301 条款加税，中国适用 12.5%",
     "对 60 个经济体以「未禁止进口强迫劳动产品」为由加税。同日 1286 清单列入"
     "中俄伊 130 所高校科研机构，含复旦、上海交大"),
    ("2026-07-28", "美", "升级", 4, "FCC 禁止外国先进机器人与联网逆变器获新认证",
     "含人形、四足机器人及联网电力逆变器，实质阻断中国企业<b>新型号</b>进入美国。"
     "存量已认证型号不受影响"),
    ("2026-07-31", "美", "升级", 4, "UFLPA 涉疆清单新增 43 家，总数 187 家",
     "2022 年实施以来最大扩容，涵盖棉花服装、有色金属、食品、交通基建、生物制药。"
     "<b>8 月 3 日起相关产品禁止进入美国</b>"),
    ("2026-08-05", "中", "升级", 4, "一天出台五项反制措施",
     "①无人机及关键零部件技术对美出口逐案从严审核 ②应用 DNA 科学等 6 家美企列入反制清单 "
     "③反制美国合规性测试公司 ④首例「对外贸易国家安全调查」⑤暂停 CCC 认证在美机构"
     "获证后工厂跟踪检查。商务部称反制「总体是克制的」"),
    ("2026-08-07", "美", "升级", 3, "多晶硅加征关税；禁止钨废料、电池废料出口",
     "以提升关键矿产供应能力为由"),
]

# 二维情景矩阵：美债长端 × 中美对抗
MATRIX = {
    ("bond_ease", "geo_ease"): dict(
        name="双缓和 · 全面修复", prob=10, color="#15803d",
        desc="30Y 回落至 5.0% 以下 + 9 月峰会延长关税休战。科技与出口链同时修复，"
             "是唯一可能出现指数级普涨的组合。上证 4050-4150。"),
    ("bond_ease", "geo_hold"): dict(
        name="估值修复 · 结构不变", prob=14, color="#16a34a",
        desc="美债缓和但中美维持现状。成长股杀估值的压力解除，但出口链与被列清单"
             "板块继续折价。上证 4000-4050，科创50 领涨。"),
    ("bond_ease", "geo_escalate"): dict(
        name="内热外冷", prob=6, color="#0369a1",
        desc="全球流动性改善但对抗升级。资金全部涌向自主可控与军工，出口链与"
             "被清单板块补跌。指数温和上行但分化极端。"),

    ("bond_hold", "geo_ease"): dict(
        name="风险偏好修复", prob=14, color="#0ea5e9",
        desc="美债僵持但峰会给出缓和。风险溢价下降先于估值修复，出口链、"
             "纺织服装、机器人有超跌反弹弹性。上证 3900-4000。"),
    ("bond_hold", "geo_hold"): dict(
        name="结构性修复（基准）", prob=21, color="#475569",
        desc="两个变量都僵持。哑铃结构延续，只有产业景气可验证的细分走独立行情。"
             "上证 3800-4050 区间震荡。<b>这是当前正在发生的状态。</b>"),
    ("bond_hold", "geo_escalate"): dict(
        name="内耗市", prob=10, color="#b45309",
        desc="美债不松、对抗加码。8 月首周的反弹被打断，资金退回红利与自主可控，"
             "成交额萎缩。上证回踩 3700-3800。"),

    ("bond_tight", "geo_ease"): dict(
        name="缓和难敌加息", prob=6, color="#c2410c",
        desc="峰会成果被美联储加息盖过。峰会当日可能有脉冲式反弹，但两三天内被"
             "分母端压力吞没。"),
    ("bond_tight", "geo_hold"): dict(
        name="二次探底", prob=12, color="#dc2626",
        desc="30Y 突破 5.45%，全球风险资产再杀估值。科创50 跌破 7 月低点，"
             "上证下探 3600-3650。"),
    ("bond_tight", "geo_escalate"): dict(
        name="双杀 · 尾部情景", prob=7, color="#7f1d1d",
        desc="加息 + 峰会破裂或推迟。杀估值叠加杀盈利预期，此前抗跌的红利资产也因"
             "流动性收紧而补跌。上证下看 3400-3500。<b>概率低但需预设应对。</b>"),
}

MATRIX_ROWS = [
    ("bond_ease", "美债缓和", "30Y < 5.00%"),
    ("bond_hold", "美债僵持", "30Y 5.0-5.4%"),
    ("bond_tight", "美债恶化", "30Y > 5.45%"),
]
MATRIX_COLS = [
    ("geo_ease", "对抗缓和", "峰会延长休战 / 撤销部分清单"),
    ("geo_hold", "维持现状", "止损峰会，象征性成果"),
    ("geo_escalate", "对抗升级", "峰会推迟或无果，新一轮清单"),
]

# 对抗敏感板块
GEO_SECTORS = [
    ("国防军工 / 信创", +0.9, "受益", "#15803d", "国产替代与自主可控逻辑的直接受益方"),
    ("稀土 / 小金属", +0.9, "受益", "#15803d", "中方手里的管制筹码，8/7 美方禁钨废料出口反向印证其战略价值"),
    ("半导体设备", +0.8, "受益", "#16a34a", "封锁越紧，国产设备验证越快"),
    ("农林牧渔", +0.2, "偏受益", "#65a30d", "峰会缓和情景下的农产品采购受益方向"),
    ("医疗服务 / CXO", -0.4, "受损", "#ea580c",
     "<b>药明康德在 1260H 名单上</b>——8 月首周涨最猛的主线之一，正好站在火线上"),
    ("光伏 / 逆变器", -0.5, "受损", "#ea580c",
     "8/7 多晶硅关税 + 7/28 FCC 禁联网逆变器新认证，两头受压"),
    ("机器人", -0.6, "受损", "#dc2626",
     "<b>FCC 7/28 禁令直接命中</b>。宇树科技 8/10 上市，且在 1260H 名单上"),
    ("家电 / 轻工", -0.6, "受损", "#dc2626", "关税与出口敞口最直接"),
    ("纺织服装", -0.8, "重度受损", "#b91c1c",
     "UFLPA 清单扩容首当其冲，8/3 起相关产品已禁止进入美国"),
]

# 修复路径情景
SCENARIOS = [
    dict(
        code="A", name="分母端缓和 · V型修复", prob=30, color="#16a34a",
        trigger="8 月底杰克逊霍尔年会，沃什给出可信的通胀应对框架，期限溢价回落",
        path="30Y 美债自 5.27% 回落 → 全球成长股估值修复 → 外资风险偏好回升 → "
             "A股科创50/创业板领涨补涨，指数收复 7 月中旬破位平台",
        target="上证 4000-4050；科创50 回到 1900 上方",
        sectors="半导体、存储、CPO/光模块（弹性最大，跌得最狠）> PCB、CRO（已先行）> 红利（跑输）",
        confirm=[
            "30Y 美债收益率 < 5.00% 且连续 5 日不创新高",
            "纳指重回 26800 以上，费城半导体指数同步走强",
            "科创50 放量突破 1800 点（本周收 1744.02）",
            "macro 因子：T1 站上 MA200 占比回升过 45%，B5 宽度推力连续为正",
        ],
    ),
    dict(
        code="B", name="分母端僵持 · 结构性修复（基准）", prob=45, color="#0369a1",
        trigger="美债在 5.0-5.4% 高位震荡，9 月是否加息悬而未决",
        path="全球资金不给高估值成长溢价，但产业景气可验证的细分靠业绩兑现走独立行情；"
             "红利提供防御，硬科技提供弹性，形成哑铃结构",
        target="上证在 3800-4050 区间震荡；不会有全面牛市，也不会二次探底",
        sectors="PCB/覆铜板、CRO/创新药（有订单和涨价数据）> 电网/核电/光伏（政策+景气）"
                "> 半导体（超跌反弹但持续性存疑）> 银行/白酒/煤炭（避险资金逐步流出）",
        confirm=[
            "两市成交额稳定在 2.2-2.7 万亿，不放大也不萎缩",
            "科创50 反弹到 1800-1850 区间遇阻回落",
            "赚钱效应在 45%-55% 之间反复（本周五仅 51%，已是这个特征）",
            "macro 因子：温度轴回升到 40-60，趋势轴在 35-55 徘徊 → 落在「震荡市」格",
        ],
    ),
    dict(
        code="C", name="分母端恶化 · 二次探底", prob=25, color="#dc2626",
        trigger="8-9 月美国通胀数据超预期，美联储 9 月真加息，市场定价「行动过晚」",
        path="30Y 美债突破 5.5% → 全球风险资产再杀估值 → A股高位科技股补跌，"
             "此前抗跌的红利资产也因流动性收紧而补跌",
        target="科创50 跌破 7 月低点；上证下探 3600-3650（年线与前期平台）",
        sectors="全面防御。现金 > 短债 > 贵金属 > 公用事业；科技与顺周期均回避",
        confirm=[
            "30Y 美债收益率 > 5.45%",
            "美股存储/AI 硬件板块连续下跌，闪迪、西部数据式的财报后暴跌扩散",
            "A股成交萎缩到 1.8 万亿以下，且赚钱效应连续 3 日低于 40%",
            "macro 因子：B4 龙头健康度跌破 45%，B1 新高新低差 < -5% → 触发顶背离否决",
        ],
    ),
]

# 观测清单
WATCH = [
    ("全市场站上 MA200 占比", "T1_breadth_ma200", "回升过 45%", "跌破 25%"),
    ("宽度推力（MA50 占比 10 日变化）", "B5_breadth_thrust", "连续为正", "转负并扩大"),
    ("龙头健康度（RPS≥90 中站上 MA20）", "B4_leader_health", "> 65%", "< 45%"),
    ("新高新低差", "B1_nhnl", "转正", "< -5%"),
    ("量能脉冲（5日/60日成交额）", "H1_turnover_pulse", "维持 > 1.10", "跌回 < 0.90"),
    ("收益离散度（P90-P50）", "H4_dispersion", "持续回落", "再度飙升"),
    ("中美对抗综合分", "geo_score", "< 50（缓和）", "> 65（对抗）"),
    ("事件强度（人工日志衰减）", "G1_event_intensity", "回落至 1.0 以下", "新增 S4 事件"),
    ("市场隐含对抗溢价", "G2_market_premium", "转负（定价缓和）", "> +8%（定价升级）"),
]

# 时间窗口
CALENDAR = [
    ("8 月 10 日", "宇树科技上市（发行价 150.8 元）", "人形机器人第一股。它同时在 1260H 名单上，"
     "且 FCC 7/28 刚禁止外国先进机器人获新认证——首日表现是观察市场如何给"
     "「被制裁的国产替代龙头」定价的样本", "#b45309"),
    ("8 月中下旬", "A股半年报密集披露期", "分子端验证：CXO 订单、光模块业绩、存储价格传导能否兑现", "#0369a1"),
    ("8 月底", "杰克逊霍尔全球央行年会 · 沃什主旨演讲", "分母端最关键窗口。沃什称演讲稿仍是「白纸一张」", "#dc2626"),
    ("9 月（拟议）", "中美元首会晤", "地缘端最关键窗口。一年期关税休战接近到期是硬约束；"
     "美国中期选举背景下特朗普需向选民展示经济成果——这两点是判断会有"
     "「战术性缓和」的主要依据", "#b91c1c"),
    ("9 月", "美联储 FOMC 议息会议", "是否加息。摩根大通预测 12 月，美银认为 9 月已是「必要之举」", "#dc2626"),
    ("9 月", "CIOE 中国光博会 · 云栖大会", "光模块/CPO 板块催化（花旗点名）", "#16a34a"),
    ("9-10 月", "欧洲 ECOC 光通信展", "CPO/NPO 共封装可见度提升", "#16a34a"),
]

SOURCES = [
    ("创业板单月历史第二跌幅！7月到底跌了谁？A股极致结构性行情复盘",
     "https://news.qq.com/rain/a/20260801A05L9E00", "腾讯新闻 · 郭一鸣 · 2026-08-01"),
    ("算力硬件、创新药两大成长主线联袂走强 机构：看好A股8月修复行情",
     "https://paper.cnstock.com/html/2026-08/05/content_2252234.htm", "上海证券报 · 2026-08-05"),
    ("美债收益率「狂飙」背后：市场开始质疑美联储信誉",
     "https://paper.cnstock.com/html/2026-08/05/content_2252235.htm", "上海证券报 · 2026-08-05"),
    ("A股收评 | 沪指低开高走涨超1% CRO与PCB掀涨停潮",
     "https://finance.sina.com.cn/stock/hkstock/hkstocknews/2026-08-07/doc-inimnrap3186038.shtml",
     "新浪财经/智通财经 · 2026-08-07"),
    ("8月5日A股收评：科创50指数大涨4.78%",
     "https://finance.cnr.cn/ycbd/20260805/t20260805_527749075.shtml", "央广网 · 2026-08-05"),
    ("8月6日收盘：美股涨跌不一，道指再创新高",
     "https://finance.sina.com.cn/stock/usstock/c/2026-08-06/doc-inimieit4875519.shtml",
     "新浪财经 · 2026-08-06"),
    ("8月7日收盘：美股收跌道指终结五连涨",
     "https://finance.sina.com.cn/stock/usstock/c/2026-08-07/doc-inimmpqv3474631.shtml",
     "新浪财经 · 2026-08-07"),
    ("A股特别提示：美联储维持利率不变但内部分歧加大，三名票委支持加息",
     "https://finance.sina.com.cn/roll/2026-07-30/doc-inikptat5284691.shtml", "新浪财经 · 2026-07-30"),
    ("三名美联储官员支持加息 内部鹰派压力升温",
     "https://www.cnfin.com/zs-lb/detail/20260803/4449850_1.html", "中国金融信息网 · 2026-08-03"),
    ("日経平均6万5000円割れ、半導体株が急落「信用の投げ売り膨らむ」",
     "https://www.nikkei.com/article/DGXZQOUB175KH0X10C26A7000000/", "日本経済新聞 · 2026-07-17"),
    ("韓国株3日 大幅反落 サムスン電子とSKハイニックス9%近く下げ",
     "https://www.nikkei.com/article/DGXZQOFL034Z20T00C26A8000000/", "日本経済新聞 · 2026-08-03"),
    ("2026年8月5日 台股因美股強彈帶動，全面大漲",
     "https://vocus.cc/article/6a735723fd897800017d0f51", "vocus · 2026-08-05"),
    ("8月3日国内A股行业主力资金流向数据报告",
     "https://stock.cngold.org/c/2026-08-03/c10665146.html", "金投网 · 2026-08-03"),
    ("7月中央政治局会议的六大亮点",
     "https://finance.sina.com.cn/jjxw/2026-07-31/doc-inikshrp4594679.shtml", "新浪财经 · 2026-07-31"),
    ("A股七月行情收官！3股逆势翻倍",
     "https://finance.sina.com.cn/stock/marketresearch/2026-07-31/doc-iniksxpf4450036.shtml",
     "新浪财经 · 2026-07-31"),
    ("一天出台五项反制措施，中美摩擦转向科技供应链",
     "https://finance.sina.com.cn/tech/roll/2026-08-06/doc-inimkwsv2911709.shtml",
     "《财经》杂志 / 新浪科技 · 2026-08-06"),
    ("中美首脑9月峰会：不是和解，而是止损",
     "https://www.borderlesscomm.com/2026/08/%E4%B8%AD%E7%BE%8E%E9%A6%96%E8%84%919%E6%9C%88%E5%B3%B0%E4%BC%9A%EF%BC%9A%E4%B8%8D%E6%98%AF%E5%92%8C%E8%A7%A3%EF%BC%8C%E8%80%8C%E6%98%AF%E6%AD%A2%E6%8D%9F/",
     "CWB 无界传播 · 毕研韬 · 2026-08-05"),
    ("商务部美大司负责人解读中美经贸磋商初步成果",
     "https://www.mofcom.gov.cn/syxwfb/art/2026/art_3e89743a8d5d4a95bccdda685830afbd.html",
     "中华人民共和国商务部"),
]


# =============================================================================
# SVG 图表
# =============================================================================

def chart_july_index(width=1000):
    """7月指数涨跌幅横向条形图"""
    rows = JULY_INDEX
    row_h, pad_t, pad_l, pad_r = 42, 22, 108, 20
    height = pad_t + len(rows) * row_h + 26
    pw = width - pad_l - pad_r
    lo, hi = -30.0, 4.0
    zero_x = pad_l + (0 - lo) / (hi - lo) * pw

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    for gx in range(-30, 5, 5):
        x = pad_l + (gx - lo) / (hi - lo) * pw
        p.append(f'<line x1="{x:.1f}" y1="{pad_t-6}" x2="{x:.1f}" y2="{pad_t+len(rows)*row_h}" '
                 f'stroke="#e8edf3" stroke-width="1"/>')
        p.append(f'<text x="{x:.1f}" y="{height-8}" text-anchor="middle" font-size="10.5" '
                 f'fill="#94a3b8">{gx}%</text>')
    p.append(f'<line x1="{zero_x:.1f}" y1="{pad_t-6}" x2="{zero_x:.1f}" '
             f'y2="{pad_t+len(rows)*row_h}" stroke="#64748b" stroke-width="1.4"/>')

    for i, (name, pct, note) in enumerate(rows):
        y = pad_t + i * row_h
        x_end = pad_l + (pct - lo) / (hi - lo) * pw
        x0, w = min(zero_x, x_end), abs(x_end - zero_x)
        col = DOWN if pct < 0 else UP
        p.append(f'<rect x="{x0:.1f}" y="{y+5}" width="{w:.1f}" height="19" rx="2.5" '
                 f'fill="{col}" opacity="0.82"/>')
        p.append(f'<text x="{pad_l-10}" y="{y+19}" text-anchor="end" font-size="13" '
                 f'font-weight="600" fill="#1e293b">{esc(name)}</text>')
        p.append(f'<text x="{x0-7:.1f}" y="{y+19}" text-anchor="end" font-size="12.5" '
                 f'font-weight="700" fill="{col}">{pct:+.1f}%</text>')
        p.append(f'<text x="{x_end+9:.1f}" y="{y+19}" font-size="11.5" fill="#64748b">{esc(note)}</text>')
    p.append('</svg>')
    return "".join(p)


def chart_july_sector(width=1000):
    """7月板块冰火两重天"""
    ups, downs = JULY_SECTOR_UP, JULY_SECTOR_DOWN
    n = max(len(ups), len(downs))
    row_h, pad_t = 34, 34
    height = pad_t + n * row_h + 20
    mid = width / 2
    half = mid - 130

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="{mid-20}" y="18" text-anchor="end" font-size="12.5" '
             f'font-weight="700" fill="{DOWN}">← 跌幅榜（科技成长）</text>')
    p.append(f'<text x="{mid+20}" y="18" font-size="12.5" font-weight="700" '
             f'fill="{UP}">涨幅榜（传统价值） →</text>')
    p.append(f'<line x1="{mid}" y1="26" x2="{mid}" y2="{pad_t+n*row_h}" stroke="#cbd5e1"/>')

    max_d = max(abs(v) for _, v in downs)
    max_u = max(v for _, v in ups)
    scale = max(max_d, max_u)

    for i, (name, pct) in enumerate(downs):
        y = pad_t + i * row_h
        w = abs(pct) / scale * half
        p.append(f'<rect x="{mid-10-w:.1f}" y="{y+4}" width="{w:.1f}" height="18" rx="2.5" '
                 f'fill="{DOWN}" opacity="0.85"/>')
        p.append(f'<text x="{mid-10-w-8:.1f}" y="{y+18}" text-anchor="end" font-size="12" '
                 f'fill="#1e293b">{esc(name)} <tspan font-weight="700" fill="{DOWN}">{pct:.1f}%</tspan></text>')

    for i, (name, pct) in enumerate(ups):
        y = pad_t + i * row_h
        w = pct / scale * half
        p.append(f'<rect x="{mid+10}" y="{y+4}" width="{w:.1f}" height="18" rx="2.5" '
                 f'fill="{UP}" opacity="0.85"/>')
        p.append(f'<text x="{mid+10+w+8:.1f}" y="{y+18}" font-size="12" fill="#1e293b">'
                 f'{esc(name)} <tspan font-weight="700" fill="{UP}">+{pct:.2f}%</tspan></text>')
    p.append('</svg>')
    return "".join(p)


def chart_aug_week(width=1000, height=326):
    """8月首周上证走势 + 成交额"""
    rows = AUG_WEEK
    # pad_t 需要给最高点上方的「涨幅 + 点位」两行标注留出空间, 否则 8/7 的标注会被裁掉
    pad_l, pad_r, pad_t, pad_b = 66, 78, 46, 46
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    closes = [r[1] for r in rows]
    lo, hi = min(closes) - 25, max(closes) + 25
    n = len(rows)
    xs = [pad_l + (i + 0.5) / n * pw for i in range(n)]

    def yy(v):
        return pad_t + ph - (v - lo) / (hi - lo) * ph

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    for f in (0, .25, .5, .75, 1):
        y = pad_t + ph * f
        v = hi - (hi - lo) * f
        p.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" y2="{y:.1f}" stroke="#e8edf3"/>')
        p.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" font-size="10.5" '
                 f'fill="#94a3b8">{v:.0f}</text>')

    # 成交额柱（右轴）
    amts = [r[5] for r in rows]
    amax = 3.0
    for i, a in enumerate(amts):
        if a is None:
            continue
        bh = a / amax * ph * 0.55
        p.append(f'<rect x="{xs[i]-22:.1f}" y="{pad_t+ph-bh:.1f}" width="44" height="{bh:.1f}" '
                 f'fill="#0ea5e9" opacity="0.16"/>')
        p.append(f'<text x="{xs[i]:.1f}" y="{pad_t+ph-bh-5:.1f}" text-anchor="middle" '
                 f'font-size="10.5" fill="#0369a1">{a:.2f}万亿</text>')

    pts = " ".join(f"{x:.1f},{yy(c):.1f}" for x, c in zip(xs, closes))
    p.append(f'<polyline points="{pts}" fill="none" stroke="#1e293b" stroke-width="2.4"/>')

    for i, (lab, close, pct, _, _, _, note) in enumerate(rows):
        x, y = xs[i], yy(close)
        col = UP if pct > 0 else DOWN
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="{col}"/>')
        p.append(f'<text x="{x:.1f}" y="{y-14:.1f}" text-anchor="middle" font-size="12" '
                 f'font-weight="700" fill="{col}">{pct:+.2f}%</text>')
        p.append(f'<text x="{x:.1f}" y="{y-28:.1f}" text-anchor="middle" font-size="11" '
                 f'fill="#475569">{close:.0f}</text>')
        p.append(f'<text x="{x:.1f}" y="{height-24}" text-anchor="middle" font-size="12" '
                 f'fill="#334155" font-weight="600">{esc(lab)}</text>')

    p.append(f'<text x="{pad_l}" y="{height-6}" font-size="11" fill="#94a3b8">'
             f'折线=上证综指收盘（8/5、8/6 为推算值） · 浅蓝柱=两市成交额</text>')
    p.append('</svg>')
    return "".join(p)


def chart_aug_index_compare(width=1000, height=210):
    """8月首周各指数周涨幅"""
    data = [("上证综指", 2.81), ("深证成指", 5.39), ("创业板指", 6.55),
            ("科创50", 13.4), ("北证50", None)]
    data = [d for d in data if d[1] is not None]
    pad_l, pad_t = 100, 18
    row_h = 38
    height = pad_t + len(data) * row_h + 26
    pw = width - pad_l - 120
    hi = 15.0

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    for gx in range(0, 16, 3):
        x = pad_l + gx / hi * pw
        p.append(f'<line x1="{x:.1f}" y1="{pad_t-4}" x2="{x:.1f}" y2="{pad_t+len(data)*row_h}" '
                 f'stroke="#e8edf3"/>')
        p.append(f'<text x="{x:.1f}" y="{height-8}" text-anchor="middle" font-size="10.5" '
                 f'fill="#94a3b8">+{gx}%</text>')
    for i, (name, pct) in enumerate(data):
        y = pad_t + i * row_h
        w = pct / hi * pw
        p.append(f'<rect x="{pad_l}" y="{y+5}" width="{w:.1f}" height="19" rx="2.5" '
                 f'fill="{UP}" opacity="0.82"/>')
        p.append(f'<text x="{pad_l-10}" y="{y+19}" text-anchor="end" font-size="13" '
                 f'font-weight="600" fill="#1e293b">{esc(name)}</text>')
        p.append(f'<text x="{pad_l+w+9:.1f}" y="{y+19}" font-size="12.5" font-weight="700" '
                 f'fill="{UP}">+{pct:.2f}%</text>')
    p.append('</svg>')
    return "".join(p)


def chart_scenarios(width=1000, height=190):
    """三情景概率带"""
    total = sum(s["prob"] for s in SCENARIOS)
    pad_l, pad_t = 20, 44
    pw = width - 2 * pad_l
    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="{pad_l}" y="24" font-size="13" font-weight="700" fill="#1e293b">'
             f'三情景概率分布（主观赋权，非模型输出）</text>')
    x = pad_l
    for s in SCENARIOS:
        w = s["prob"] / total * pw
        p.append(f'<rect x="{x:.1f}" y="{pad_t}" width="{w-3:.1f}" height="52" rx="6" '
                 f'fill="{s["color"]}" opacity="0.88"/>')
        p.append(f'<text x="{x+w/2:.1f}" y="{pad_t+24}" text-anchor="middle" font-size="16" '
                 f'font-weight="700" fill="#fff">{s["prob"]}%</text>')
        p.append(f'<text x="{x+w/2:.1f}" y="{pad_t+42}" text-anchor="middle" font-size="12" '
                 f'fill="#fff" opacity="0.95">情景 {s["code"]}</text>')
        p.append(f'<text x="{x+w/2:.1f}" y="{pad_t+74}" text-anchor="middle" font-size="12.5" '
                 f'font-weight="600" fill="{s["color"]}">{esc(s["name"])}</text>')
        p.append(f'<text x="{x+w/2:.1f}" y="{pad_t+94}" text-anchor="middle" font-size="11" '
                 f'fill="#64748b">{esc(s["target"].split("；")[0])}</text>')
        x += w
    p.append('</svg>')
    return "".join(p)


def chart_geo_timeline(width=1000):
    """中美对抗事件时间线 — 高度按 severity，颜色按发起方"""
    rows = GEO_TIMELINE
    pad_l, pad_r, pad_t = 62, 20, 52
    row_h = 30
    height = pad_t + len(rows) * row_h + 34
    pw = width - pad_l - pad_r
    side_col = {"美": "#b91c1c", "中": "#1d4ed8", "双": "#15803d"}

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="{pad_l}" y="20" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'中美对抗事件时间线（2026年5月 — 8月）</text>')
    p.append(f'<text x="{pad_l}" y="38" font-size="11.5" fill="#64748b">'
             f'条形长度 = 事件烈度 S1-S5 ｜ '
             f'<tspan fill="#b91c1c">红=美方措施</tspan> '
             f'<tspan fill="#1d4ed8">蓝=中方反制</tspan> '
             f'<tspan fill="#15803d">绿=双边缓和</tspan></text>')

    for i, (date, side, direction, sev, title, _) in enumerate(rows):
        y = pad_t + i * row_h
        col = side_col.get(side, "#64748b")
        w = sev / 5.0 * (pw * 0.30)
        op = 0.9 if direction == "升级" else 0.55
        p.append(f'<text x="{pad_l-8}" y="{y+15}" text-anchor="end" font-size="11" '
                 f'fill="#64748b">{esc(date[5:])}</text>')
        p.append(f'<rect x="{pad_l}" y="{y+4}" width="{w:.1f}" height="16" rx="2.5" '
                 f'fill="{col}" opacity="{op}"/>')
        p.append(f'<text x="{pad_l+6}" y="{y+16}" font-size="11" fill="#fff" '
                 f'font-weight="700">S{sev}</text>')
        arrow = "↑" if direction == "升级" else "↓"
        p.append(f'<text x="{pad_l+w+9:.1f}" y="{y+16}" font-size="12" fill="#1e293b">'
                 f'<tspan fill="{col}" font-weight="700">[{side}]{arrow}</tspan> '
                 f'{esc(title)}</text>')

    p.append('</svg>')
    return "".join(p)


def chart_matrix(width=1000, height=430):
    """二维情景矩阵热力图"""
    pad_l, pad_t = 132, 74
    cw = (width - pad_l - 22) / 3
    ch = (height - pad_t - 26) / 3

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="20" y="22" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'二维情景矩阵：美债长端 × 中美对抗</text>')
    p.append(f'<text x="20" y="40" font-size="11.5" fill="#64748b">'
             f'格内数字为主观概率（合计 100%）。方块大小与颜色深浅同步反映概率。</text>')

    for j, (ck, cname, cdesc) in enumerate(MATRIX_COLS):
        x = pad_l + j * cw
        p.append(f'<text x="{x+cw/2:.1f}" y="{pad_t-24}" text-anchor="middle" '
                 f'font-size="13" font-weight="700" fill="#1e293b">{esc(cname)}</text>')
        p.append(f'<text x="{x+cw/2:.1f}" y="{pad_t-8}" text-anchor="middle" '
                 f'font-size="10.5" fill="#94a3b8">{esc(cdesc)}</text>')

    for i, (rk, rname, rdesc) in enumerate(MATRIX_ROWS):
        y = pad_t + i * ch
        p.append(f'<text x="{pad_l-12}" y="{y+ch/2-4:.1f}" text-anchor="end" '
                 f'font-size="13" font-weight="700" fill="#1e293b">{esc(rname)}</text>')
        p.append(f'<text x="{pad_l-12}" y="{y+ch/2+13:.1f}" text-anchor="end" '
                 f'font-size="10.5" fill="#94a3b8">{esc(rdesc)}</text>')

        for j, (ck, _, _) in enumerate(MATRIX_COLS):
            cell = MATRIX[(rk, ck)]
            x = pad_l + j * cw
            op = 0.14 + cell["prob"] / 25.0 * 0.55
            p.append(f'<rect x="{x+3:.1f}" y="{y+3:.1f}" width="{cw-6:.1f}" '
                     f'height="{ch-6:.1f}" rx="7" fill="{cell["color"]}" '
                     f'opacity="{op:.2f}" stroke="{cell["color"]}" stroke-width="1.1"/>')
            p.append(f'<text x="{x+cw/2:.1f}" y="{y+ch/2-14:.1f}" text-anchor="middle" '
                     f'font-size="25" font-weight="700" fill="{cell["color"]}">'
                     f'{cell["prob"]}%</text>')
            nm = cell["name"]
            if " · " in nm:
                a, b = nm.split(" · ", 1)
                p.append(f'<text x="{x+cw/2:.1f}" y="{y+ch/2+8:.1f}" text-anchor="middle" '
                         f'font-size="12.5" font-weight="600" fill="#1e293b">{esc(a)}</text>')
                p.append(f'<text x="{x+cw/2:.1f}" y="{y+ch/2+25:.1f}" text-anchor="middle" '
                         f'font-size="11.5" fill="#475569">{esc(b)}</text>')
            else:
                p.append(f'<text x="{x+cw/2:.1f}" y="{y+ch/2+12:.1f}" text-anchor="middle" '
                         f'font-size="12.5" font-weight="600" fill="#1e293b">{esc(nm)}</text>')

    # 当前位置标记
    cx = pad_l + 1 * cw + cw / 2
    cy = pad_t + 1 * ch + ch / 2
    p.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{min(cw,ch)/2-6:.1f}" fill="none" '
             f'stroke="#0f172a" stroke-width="2.6" stroke-dasharray="6,4"/>')
    p.append('</svg>')
    return "".join(p)


def chart_geo_sectors(width=1000):
    """板块对抗敏感度"""
    rows = GEO_SECTORS
    pad_t = 34
    row_h = 34
    height = pad_t + len(rows) * row_h + 24
    # 标签固定在左栏 (x<=150), 零轴放在 380, 单边最长条 200px ——
    # 这样即使敏感度 -0.8 的最长负向条也只延伸到 x=180, 不会压到标签。
    label_x = 20
    mid = 380
    half = 200

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="{label_x}" y="18" font-size="13" font-weight="700" fill="#1e293b">'
             f'板块对抗敏感度（对抗升级时的方向与幅度）</text>')
    p.append(f'<line x1="{mid}" y1="{pad_t-6}" x2="{mid}" y2="{pad_t+len(rows)*row_h}" '
             f'stroke="#cbd5e1" stroke-width="1.3"/>')

    for i, (name, sens, label, col, note) in enumerate(rows):
        y = pad_t + i * row_h
        w = abs(sens) * half
        x0 = mid if sens > 0 else mid - w
        p.append(f'<text x="{label_x}" y="{y+18}" font-size="12.5" '
                 f'font-weight="600" fill="#1e293b">{esc(name)}</text>')
        p.append(f'<rect x="{x0:.1f}" y="{y+5}" width="{w:.1f}" height="18" rx="2.5" '
                 f'fill="{col}" opacity="0.85"/>')
        tx = mid + w + 8 if sens > 0 else mid - w - 8
        anchor = "start" if sens > 0 else "end"
        p.append(f'<text x="{tx:.1f}" y="{y+18}" text-anchor="{anchor}" font-size="11.5" '
                 f'font-weight="700" fill="{col}">{sens:+.1f} {esc(label)}</text>')
    p.append('</svg>')
    return "".join(p)


def chart_transmission(width=1000, height=250):
    """传导链路图"""
    boxes = [
        (30, 96, 168, 56, "#7c2d12", "沃什鹰派 + 压缩沟通", "3 票反对支持加息"),
        (236, 96, 168, 56, "#9a3412", "美债期限溢价上行", "30Y 5.27%｜10Y 4.7%"),
        (442, 96, 168, 56, "#b45309", "全球成长股杀估值", "分母端，非分子端"),
        (648, 40, 160, 52, "#166534", "日经 7/17 -4.0%", "跌破 65000"),
        (648, 100, 160, 52, "#166534", "KOSPI 8/3 -5.12%", "三星 -7.5%"),
        (648, 160, 160, 52, "#166534", "A股 7/17 -3.05%", "创业板月跌 23%"),
    ]
    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="30" y="26" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'7 月下跌的传导链路 —— 起点在华盛顿，不在北京</text>')
    p.append(f'<text x="30" y="46" font-size="11.5" fill="#64748b">'
             f'同一天（7/17）A股与日股同步暴跌，是判断「这不是 A 股自身问题」最直接的证据</text>')
    for x, y, w, h, c, t1, t2 in boxes:
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{c}" opacity="0.13" '
                 f'stroke="{c}" stroke-width="1.3"/>')
        p.append(f'<text x="{x+w/2}" y="{y+23}" text-anchor="middle" font-size="12.5" '
                 f'font-weight="700" fill="{c}">{esc(t1)}</text>')
        p.append(f'<text x="{x+w/2}" y="{y+41}" text-anchor="middle" font-size="11" '
                 f'fill="#475569">{esc(t2)}</text>')
    for x0, x1 in [(198, 236), (404, 442)]:
        p.append(f'<path d="M{x0},124 L{x1-6},124" stroke="#94a3b8" stroke-width="1.8" '
                 f'marker-end="url(#ar)"/>')
    for ty in (66, 126, 186):
        p.append(f'<path d="M610,124 Q628,124 628,{ty} L642,{ty}" stroke="#94a3b8" '
                 f'stroke-width="1.5" fill="none" marker-end="url(#ar)"/>')
    p.append('<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="7" refY="3" '
             'orient="auto"><path d="M0,0 L7,3 L0,6" fill="#94a3b8"/></marker></defs>')
    p.append('</svg>')
    return "".join(p)


# =============================================================================
# HTML
# =============================================================================

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:28px 34px 70px;background:#f6f8fa;color:#0f172a;
     font-family:%s;font-size:14.5px;line-height:1.72;max-width:1180px}
h1{font-size:25px;margin:0 0 6px;letter-spacing:-.3px}
h2{font-size:17px;margin:36px 0 12px;padding-bottom:9px;border-bottom:2px solid #dde4ec;
   color:#0f172a}
h3{font-size:14.5px;margin:22px 0 8px;color:#334155}
.sub{color:#64748b;font-size:13px;margin-bottom:24px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:11px;padding:18px 22px;
      margin-bottom:16px}
.lead{background:#0f172a;color:#fff;border-radius:11px;padding:22px 26px;margin-bottom:20px}
.lead b{color:#fbbf24}
.lead .h{font-size:17px;font-weight:650;margin-bottom:8px}
.lead p{margin:0 0 10px;opacity:.94;font-size:14px}
.lead p:last-child{margin-bottom:0}
.kpis{display:flex;gap:13px;flex-wrap:wrap;margin-bottom:18px}
.kpi{flex:1;min-width:158px;background:#fff;border:1px solid #e2e8f0;border-radius:10px;
     padding:13px 16px}
.kpi .k{font-size:11.5px;color:#64748b}
.kpi .v{font-size:24px;font-weight:660;margin-top:2px;line-height:1.2}
.kpi .n{font-size:11.5px;color:#94a3b8;margin-top:1px}
table{width:100%%;border-collapse:collapse;font-size:13px;margin:6px 0}
th{background:#f1f5f9;text-align:left;padding:9px 11px;font-weight:600;color:#475569;
   border-bottom:1px solid #e2e8f0}
td{padding:8px 11px;border-bottom:1px solid #f1f5f9;vertical-align:top}
tr:hover td{background:#fafcfe}
.num{text-align:right;font-variant-numeric:tabular-nums}
.up{color:#dc2626;font-weight:650}
.dn{color:#16a34a;font-weight:650}
.sc{border-left:5px solid;border-radius:9px;background:#fff;border-top:1px solid #e2e8f0;
    border-right:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;padding:17px 21px;
    margin-bottom:15px}
.sc .t{font-size:16px;font-weight:660;margin-bottom:3px}
.sc .p{display:inline-block;padding:2px 11px;border-radius:20px;font-size:12px;
       font-weight:660;color:#fff;margin-left:9px;vertical-align:2px}
.sc dl{margin:11px 0 0;display:grid;grid-template-columns:82px 1fr;gap:6px 14px;font-size:13.5px}
.sc dt{color:#64748b;font-weight:600}
.sc dd{margin:0}
.sc ul{margin:7px 0 0;padding-left:19px;font-size:13px;color:#334155}
.sc li{margin-bottom:3px}
.note{color:#64748b;font-size:12.5px;margin-top:9px}
.warn{background:#fffbeb;border:1px solid #fde68a;color:#78350f;padding:13px 17px;
      border-radius:9px;margin:14px 0;font-size:13.5px}
.tag{display:inline-block;padding:1px 9px;border-radius:20px;font-size:11.5px;font-weight:600}
.t-lead{background:#dcfce7;color:#15803d}
.t-emg{background:#e0f2fe;color:#0369a1}
.t-fade{background:#ffedd5;color:#c2410c}
.src{font-size:12.5px;color:#475569}
.src a{color:#0369a1;text-decoration:none}
.src a:hover{text-decoration:underline}
.src li{margin-bottom:5px}
""" % FONT


def kpi(k, v, n, color="#0f172a"):
    return (f'<div class="kpi"><div class="k">{esc(k)}</div>'
            f'<div class="v" style="color:{color}">{v}</div>'
            f'<div class="n">{esc(n)}</div></div>')


def build():
    kpis = "".join([
        kpi("上证综指 · 7月", "-6.4%", "收 3832.26，回吐年内涨幅", DOWN),
        kpi("创业板指 · 7月", "-23%", "历史第二大单月跌幅", DOWN),
        kpi("上证综指 · 8月首周", "+2.81%", "收 3940.04", UP),
        kpi("创业板指 · 8月首周", "+6.55%", "科创50 周内两度暴涨超 4%", UP),
        kpi("30Y 美债收益率", "5.27%", "2007 年以来新高", "#b45309"),
    ])

    aug_rows = "".join(
        f'<tr><td><b>{esc(r[0])}</b></td>'
        f'<td class="num">{r[1]:.2f}</td>'
        f'<td class="num {"up" if r[2]>0 else "dn"}">{r[2]:+.2f}%</td>'
        f'<td class="num">{f"{r[3]:+.2f}%" if r[3] is not None else "—"}</td>'
        f'<td class="num">{f"{r[4]:+.2f}%" if r[4] is not None else "—"}</td>'
        f'<td class="num">{f"{r[5]:.2f}" if r[5] is not None else "—"}</td>'
        f'<td>{esc(r[6])}</td></tr>'
        for r in AUG_WEEK
    )

    ov_rows = "".join(
        f'<tr><td><b>{esc(a)}</b></td><td class="num">{esc(b)}</td>'
        f'<td class="num" style="color:{e}">{esc(c)}</td><td>{esc(d)}</td></tr>'
        for a, b, c, d, e in OVERSEAS
    )

    sc_html = ""
    for s in SCENARIOS:
        conf = "".join(f"<li>{esc(c)}</li>" for c in s["confirm"])
        sc_html += f"""
<div class="sc" style="border-left-color:{s['color']}">
  <div class="t" style="color:{s['color']}">情景 {s['code']} · {esc(s['name'])}
    <span class="p" style="background:{s['color']}">{s['prob']}%</span></div>
  <dl>
    <dt>触发</dt><dd>{esc(s['trigger'])}</dd>
    <dt>路径</dt><dd>{esc(s['path'])}</dd>
    <dt>指数位置</dt><dd><b>{esc(s['target'])}</b></dd>
    <dt>板块次序</dt><dd>{esc(s['sectors'])}</dd>
  </dl>
  <div style="margin-top:11px"><b style="font-size:13px;color:{s['color']}">可观测验证条件</b>
  <ul>{conf}</ul></div>
</div>"""

    geo_rows = "".join(
        f'<tr><td style="white-space:nowrap"><b>{esc(d)}</b></td>'
        f'<td style="text-align:center"><span class="tag" style="background:'
        f'{"#fee2e2;color:#b91c1c" if s=="美" else ("#dbeafe;color:#1d4ed8" if s=="中" else "#dcfce7;color:#15803d")}">'
        f'{esc(s)}{"↑" if dr=="升级" else "↓"}</span></td>'
        f'<td style="text-align:center"><b>S{sev}</b></td>'
        f'<td><b>{esc(t)}</b></td><td style="color:#475569">{detail}</td></tr>'
        for d, s, dr, sev, t, detail in GEO_TIMELINE
    )

    matrix_cards = ""
    for rk, rname, rdesc in MATRIX_ROWS:
        for ck, cname, _ in MATRIX_COLS:
            cell = MATRIX[(rk, ck)]
            if cell["prob"] < 10:
                continue
            matrix_cards += (
                f'<div class="sc" style="border-left-color:{cell["color"]}">'
                f'<div class="t" style="color:{cell["color"]};font-size:14.5px">'
                f'{esc(cell["name"])}'
                f'<span class="p" style="background:{cell["color"]}">{cell["prob"]}%</span></div>'
                f'<div style="font-size:12.5px;color:#64748b;margin:2px 0 7px">'
                f'{esc(rname)} × {esc(cname)}</div>'
                f'<div style="font-size:13.5px">{cell["desc"]}</div></div>'
            )

    watch_rows = "".join(
        f'<tr><td>{esc(a)}</td><td><code style="font-size:12px">{esc(b)}</code></td>'
        f'<td class="up">{esc(c)}</td><td class="dn">{esc(d)}</td></tr>'
        for a, b, c, d in WATCH
    )

    cal_rows = "".join(
        f'<tr><td style="white-space:nowrap"><b style="color:{col}">{esc(t)}</b></td>'
        f'<td><b>{esc(ev)}</b></td><td>{esc(why)}</td></tr>'
        for t, ev, why, col in CALENDAR
    )

    src = "".join(
        f'<li><a href="{u}" target="_blank">{esc(t)}</a> <span style="color:#94a3b8">· {esc(s)}</span></li>'
        for t, u, s in SOURCES
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股 2026年7月-8月首周复盘与修复路径推演</title><style>{CSS}</style></head><body>

<h1>A股 2026 年 7 月 — 8 月首周复盘与修复路径推演</h1>
<div class="sub">数据截至 2026-08-07 收盘 · 生成于 {datetime.now():%Y-%m-%d %H:%M} ·
全部数据来自公开报道，来源见文末</div>

<div class="lead">
  <div class="h">一句话结论</div>
  <p>7 月的暴跌<b>不是 A 股自己的问题</b>，是一次全球 AI 硬件资产的<b>分母端估值重置</b>——
  起点是美联储新主席沃什的鹰派转向推高美债期限溢价，30Y 美债冲上 5.27% 的 2007 年以来新高。
  最直接的证据是 <b>7 月 17 日 A 股跌 3.05% 的同一天，日经跌 4.0%</b>；8 月 3 日 KOSPI 跌 5.12%，
  三星、SK 海力士双双跌超 6.9%。</p>
  <p>与此同时，<b>分子端反而在改善</b>：北美四大云厂商把 2026 年资本开支指引上调至 7200-7450 亿美元
  且可见性延伸到 2028 年；电子布年内涨价 118%-165%；药明康德半年报收入 +38.9% 并上调全年指引。
  跌的是估值，不是景气。</p>
  <p>因此 8 月首周的反弹（上证 +2.81%、创业板 +6.55%）有产业逻辑支撑，
  但持续性取决于外部环境，而不是 A 股自己。</p>
  <p style="margin-bottom:0"><b>但只看美债是不够的——还有第二个变量。</b>
  2026 年 6 月以来中美摩擦已从关税转向科技供应链与实体清单：
  7/28 FCC 禁外国先进机器人获新认证、7/31 涉疆清单单次扩容 43 家、
  <b>8/5 中方一天出台五项反制</b>。9 月还有元首会晤。
  这条线对<b>板块</b>的影响远大于对指数的影响，用一维推演会系统性漏掉结构性风险。
  <b>8 月底杰克逊霍尔（分母端）与 9 月元首会晤（地缘端）是决定路径的两个节点。</b></p>
</div>

<div class="kpis">{kpis}</div>

<h2>一、7 月：指数熊、个股牛的极致分化</h2>
<div class="card">{chart_july_index()}
<div class="note">上证 50 的 -1.5% 为估算值（原文表述为「相对抗跌，成为压舱石」，未给出具体数字）。</div></div>

<div class="card">
  <p style="margin-top:0">7 月最反直觉的地方是<b>指数跌得极惨，但个股涨多跌少</b>：全月超 2500 只个股上涨，
  7 月 31 日收官当天近 4700 只上涨、101 只涨停、<b>零跌停</b>。</p>
  <p style="margin-bottom:0">这意味着 7 月真正下跌的只有两类标的：一是双创指数本身，二是上半年持续走高的高位科技股。
  兆易创新、佰维存储、德明利单月最大跌幅均超 50%，西安奕材接近 -60%。
  <b>只有重仓高位科技赛道的账户才出现明显回撤。</b></p>
</div>

<h3>板块：冰火两重天</h3>
<div class="card">{chart_july_sector()}
<div class="note">申万 31 个一级行业中煤炭以 +14.29% 居首；建设银行、工商银行、皖通高速、粤高速 A 等
「老登股」纷纷创历史新高。资金从科技主线大规模撤离涌入低位红利，形成极致跷跷板。</div></div>

<h2>二、传导链路：这轮下跌的起点在哪</h2>
<div class="card">{chart_transmission()}</div>

<div class="card">
<table><thead><tr><th style="width:130px">环节</th><th>事实</th></tr></thead><tbody>
<tr><td><b>美联储</b></td><td>7 月 29-30 日 FOMC 维持利率 3.50%-3.75% 不变（连续第五次按兵不动），
但出现 <b>3 张同向反对票</b>——哈马克、卡什卡利、洛根均主张加息 25bp，为 2016 年以来首次。
主席凯文·沃什称对通胀「感到担忧，若有需要将毫不犹豫采取行动」。</td></tr>
<tr><td><b>沟通事故</b></td><td>沃什在会上提出<b>调整议息会议频率</b>的设想，若落地将压缩市场获取利率路径的
关键信息窗口。中金认为这是把紧缩功能「外包」给市场，削弱政策信任；天风认为政策路径能见度下降，
长端需要更高的不确定性风险补偿。</td></tr>
<tr><td><b>债市反应</b></td><td>30Y 美债收益率飙至 <b>5.27%</b>（2007 年以来新高），10Y 站上 4.7%（年内峰值）。
7 月 10Y 单月上行约 32bp，是今年 3 月以来最剧烈的月度下跌。</td></tr>
<tr><td><b>加息预期</b></td><td>摩根大通 8 月 3 日将加息时点<b>从 2027 下半年提前至 2026 年 12 月</b>，
并上调 2026 年底 10Y 目标至 4.85%、30Y 至 5.4%。美银认为若通胀数据不够鸽派，9 月加息
「可能已不只是政策选项，更是挽回市场信任的必要之举」。CME 显示年底前加息概率 26.3%。</td></tr>
<tr><td><b>其他推手</b></td><td>国盛熊园列出三重原因：美联储抗通胀信誉受损、美日联合干预日元
（可能需抛售或抵押美债换流动性）、美伊局势反复推升通胀风险。</td></tr>
</tbody></table></div>

<h2>三、8 月首周：超跌修复启动，但赚钱效应只有 51%</h2>
<div class="card">{chart_aug_week()}</div>
<div class="card">
<table><thead><tr><th>交易日</th><th class="num">上证收盘</th><th class="num">上证</th>
<th class="num">创业板</th><th class="num">科创50</th><th class="num">成交额(万亿)</th>
<th>盘面与资金</th></tr></thead><tbody>{aug_rows}</tbody></table>
<div class="note">8/5、8/6 上证收盘点位由相邻交易日涨跌幅反推，与官方数据可能有小数级差异；
8/6 无直接报道，涨幅为推算。其余数据均来自公开收评。</div></div>

<div class="card">{chart_aug_index_compare()}
<div class="note">科创50 周涨幅由 8/4(+4.09%)、8/5(+4.78%)、8/7(+2.51%) 三日已知涨幅粗略累计估算，
仅供比较量级，非精确值。</div></div>

<h3>本周的驱动是实打实的产业催化，不是纯情绪</h3>
<div class="card"><table><thead><tr><th style="width:150px">主线</th><th>催化</th></tr></thead><tbody>
<tr><td><b>PCB / 覆铜板</b></td><td>花旗：8 月电子布 1080、2116、7628 系列均价环比<b>上涨 17%-18%</b>，
年内累计涨幅 <b>118%-165%</b>，价格沿「电子布→覆铜板→PCB」链条传导。高盛上调 AI 服务器 PCB 与 CCL 预测，
预计 2028 年市场规模分别达 840 亿、480 亿美元，英伟达 Rubin 架构带动需求。</td></tr>
<tr><td><b>CRO / 创新药</b></td><td>药明康德半年报收入 289 亿（<b>+38.9%</b>）、归母净利 110.8 亿（+29.4%），
在手订单 664.3 亿（+25.2%），上调全年收入指引至 585-605 亿。国金证券：截至 7 月 10 日 TOP15 跨国药企
外部资本开支已达 2003 亿美元，接近 2025 全年的 73.5%；2026 上半年中国创新药 License-out 出海总额
约 <b>997 亿美元，约为 2024 全年的 2 倍</b>。</td></tr>
<tr><td><b>光模块 / CPO</b></td><td>TrendForce：英伟达已向部分伙伴出货新一代 Spectrum-X CPO 交换机，
博通 51.2T Bailly CPO 交换机小量持续出货，<b>CPO 正式迈入量产阶段</b>。北美四大云厂商全年资本开支
合计指引 <b>7200-7450 亿美元</b>，谷歌、Meta、亚马逊均上调，花旗称可见性已延伸至 2028 年。</td></tr>
</tbody></table></div>

<h3>资金流：从半导体出逃到回流，只用了一天</h3>
<div class="card"><table><thead><tr><th style="width:100px">日期</th><th>主力资金动向</th></tr></thead><tbody>
<tr><td><b>8/3</b></td><td><span class="tag t-fade">流出</span> 半导体净流出 <b>200.6 亿</b>居首，
玻璃玻纤、消费电子次之；资金转向电力、核电、电网设备、光通信、光伏设备、自动化机器人。</td></tr>
<tr><td><b>8/4</b></td><td><span class="tag t-lead">流入</span> 两市主力净流入 <b>538.1 亿</b>
（创业板 279.54 亿、沪深 300 成分 259.23 亿）。申万二级中通信设备 <b>+221 亿</b>居首，
元件、半导体各超 100 亿；「易中天」三只合计净流入超 125 亿。</td></tr>
</tbody></table>
<div class="warn"><b>值得警惕的一点</b>：8 月 7 日全市场 2856 只上涨、2536 只下跌，
<b>赚钱效应仅约 51%</b>，涨幅高度集中在医药和电子硬件两个方向。
同日跨境支付、金融科技、软件开发、IT 服务、铁路公路、多元金融跌幅居前——
<b>这是典型的「结构性修复」而非「全面修复」的盘面特征。</b></div></div>

<h2>四、海外：同一根导火索，不同的表现</h2>
<div class="card"><table><thead><tr><th style="width:150px">市场</th><th class="num">最新点位</th>
<th class="num">近期变动</th><th>说明</th></tr></thead><tbody>{ov_rows}</tbody></table>
<div class="note">日经 7 月 8 日收 66819.05（-1437.91 点），7 月 17 日跌 2694 点（-4.0%）至 64141，
跌破 65000；8 月 6-7 日 AI/半导体软调，日经小幅续落。台股 8/3 外资重回卖超、投信单独承接，
焦点由台积电转向联发科、日月光投控与创意。</div></div>

<div class="card">
<h3 style="margin-top:0">一个容易被忽略的背离</h3>
<p>本周后半段，<b>美股在跌，A 股在涨</b>：8 月 6 日道指 -0.85%、纳指 -0.06%，闪迪与西部数据财报后大跌，
存储芯片重挫；同一时段 A 股 8/6 微涨、8/7 大涨 1.02%，科创 50 领涨 2.51%。</p>
<p style="margin-bottom:0">这说明 8 月首周 A 股的反弹<b>更多来自自身超跌修复与国内产业催化</b>
（电子布涨价、CRO 订单、半年报验证），而非美股带动。这是好消息也是坏消息——
好消息是 A 股有了一定的独立定价能力；坏消息是<b>如果分母端继续恶化，这种独立性很难长期维持</b>，
2024 年以来的多次经验表明 A 股高估值成长最终仍会跟随全球利率环境。</p>
</div>

<h2>五、国内政策：分子端的对冲力量</h2>
<div class="card">
<p style="margin-top:0">7 月 30 日中央政治局会议表述相比前两次<b>更显积极</b>，主动提出「加大逆周期调节力度」、
「及时谋划出台务实管用的增量政策」。</p>
<table><thead><tr><th style="width:110px">维度</th><th>表述与含义</th></tr></thead><tbody>
<tr><td><b>财政</b></td><td>强调「加快财政支出和债券资金使用进度」，意味着节奏或由上半年的<b>支出克制转向下半年加速发力</b>；
投向新在「六张网」。</td></tr>
<tr><td><b>货币</b></td><td>延续「适度宽松」基调，但<b>新增</b>「综合运用并适时调整货币政策工具」的表述。</td></tr>
<tr><td><b>资本市场</b></td><td>产业、内需、地产、资本市场多条政策明确，对 A 股形成全方位指引。</td></tr>
</tbody></table>
<div class="note">这是本轮推演中<b>唯一能部分对冲外部分母端压力的变量</b>。
如果 9 月前有实质性的降准或专项债提速落地，可以把基准情景 B 向情景 A 推动。</div></div>

<h2>六、第二个变量：中美对抗</h2>
<div class="warn"><b>这一节是对上一版推演的修正。</b>
上一版把修复路径压缩成「美债长端往哪走」一个变量，这是不完整的。
2026 年 6 月以来中美摩擦已从关税转向<b>科技供应链与实体清单</b>，
而 8 月 5 日中方一天出台五项反制、9 月又有元首会晤——这条线对板块的影响
比对指数的影响大得多，用一维推演会系统性地漏掉结构性风险。</div>

<div class="card">{chart_geo_timeline()}</div>

<div class="card"><table><thead><tr><th style="width:88px">日期</th>
<th style="width:52px">方向</th><th style="width:44px">烈度</th>
<th style="width:270px">事件</th><th>说明</th></tr></thead>
<tbody>{geo_rows}</tbody></table>
<div class="note">摩擦密度明显高于以往，且<b>正从广泛的关税壁垒转向关键供应链的卡位</b>。
分析人士认为目前仍处于「斗而不破」阶段，双方焦点从传统关税转向具体的企业清单与市场认证壁垒。</div></div>

<div class="card">
<h3 style="margin-top:0">为什么这条线必须单独当一个变量</h3>
<p style="margin-top:0">美债影响的是<b>分母（估值）</b>，均匀作用于全市场；
中美对抗影响的是<b>分子（特定板块的盈利预期）和风险溢价</b>，是精确打击。
两者的传导机制、受影响对象、时间尺度都不同，混在一起会互相抵消。</p>
<p style="margin-bottom:0">更重要的是：<b>9 月会晤的时间是已知的，结果是未知的。</b>
这类「已知日程 + 未知结果」的事件，正确的应对不是猜方向，而是在窗口内降低敞口——
期望收益不变但方差骤增时，降仓是唯一理性的选择。</p></div>

<h3>板块层面的非对称影响</h3>
<div class="card">{chart_geo_sectors()}</div>
<div class="card"><table><thead><tr><th style="width:150px">板块</th>
<th style="width:70px">敏感度</th><th>说明</th></tr></thead><tbody>
{"".join(f'<tr><td><b>{esc(n)}</b></td><td class="num" style="color:{c};font-weight:700">{s:+.1f}</td><td>{note}</td></tr>' for n, s, _, c, note in GEO_SECTORS)}
</tbody></table>
<div class="warn" style="margin-bottom:0"><b>两个必须警惕的交叉点：</b><br>
① <b>CRO / 医疗服务</b>是 8 月首周涨最猛的主线（8/7 单日涨超 10%），
但<b>药明康德在 6 月 8 日就被列入了 1260H 名单</b>。业绩逻辑与地缘风险同时存在于同一个标的上。<br>
② <b>机器人</b>板块：FCC 7/28 禁令直接命中人形与四足机器人，
而宇树科技 <b>8 月 10 日上市</b>且同样在 1260H 名单上。首日定价会是一个重要的市场态度样本。</div></div>

<h2>七、二维情景矩阵</h2>
<div class="card">{chart_matrix()}
<div class="note">虚线圈 = 当前所处位置（美债僵持 × 维持现状）。
<b>注意两个变量并非独立</b>：对抗升级会推高关税与供应链成本，进而抬升美国通胀预期，
使美联储更鹰——右下角三格的实际联合概率可能比表中的乘积略高。
概率为主观赋权，不是模型输出。</div></div>

{matrix_cards}

<div class="card">
<h3 style="margin-top:0">对 9 月会晤的判断</h3>
<p style="margin-top:0">分析普遍认为这<b>不会是「和解峰会」，而是「止损峰会」</b>——
目标是在竞争持续的前提下找到新的稳定边界。支持「至少有战术性缓和」的两条硬逻辑：</p>
<p style="margin:0 0 6px"><b>1. 一年期关税休战安排接近到期</b>，
延长暂停期限是双方最容易展示的成果，也是成本最低的交易。</p>
<p style="margin:0 0 6px"><b>2. 美国中期选举背景下</b>，
特朗普政府需要向选民展示具体的经济成果；农业州与出口行业希望恢复稳定市场。</p>
<p style="margin-bottom:0">但要看清楚：经贸成果可能明显，<b>却不会改变竞争结构</b>。
采购协议改善贸易数字，解决不了产业竞争；芯片、算力和 AI 已被纳入国家安全框架，
不再是商业问题。所以「对抗缓和」列的合理概率是 30%，而不是更高。</p></div>

<h2>八、修复路径推演（美债维度）</h2>
<div class="card">{chart_scenarios()}
<div class="note">这三个情景是上面矩阵按<b>行</b>汇总的结果（美债维度的边际概率 30/45/25）。
保留一维视角是因为它更好记，但做板块选择时必须回到二维矩阵。</div></div>

{sc_html}

<h2>九、可观测的验证清单</h2>
<div class="card">
<p style="margin-top:0">下面这些指标可以直接用 <code>run_macro_regime.py</code> 每日算出来，
对照阈值判断当前落在哪个情景，<b>不需要依赖任何观点</b>：</p>
<table><thead><tr><th>观测项</th><th style="width:210px">对应因子</th>
<th style="width:120px">修复确认</th><th style="width:120px">恶化警戒</th></tr></thead>
<tbody>{watch_rows}</tbody></table>
<div class="note">运行方式：<code>py -3 run_macro_regime.py --parquet output/tdx_stock_daily_with_rps.parquet</code>
，因子定义见 <code>docs/strategy_macro_regime.md</code>。</div></div>

<div class="card">
<h3 style="margin-top:0">板块四象限的当前判断</h3>
<table><thead><tr><th style="width:110px">象限</th><th style="width:230px">板块</th><th>理由</th></tr></thead><tbody>
<tr><td><span class="tag t-lead">主升 LEADING</span></td><td><b>PCB / 覆铜板、CRO / 创新药</b></td>
<td>有涨价数据和订单数据双重验证，8 月首周已用涨停潮确认，是当前唯一「强且在加速」的方向</td></tr>
<tr><td><span class="tag t-emg">启动 EMERGING</span></td><td><b>光模块 / CPO、半导体 / 存储</b></td>
<td>跌幅最深（半导体 -36.45%），CPO 进入量产、云厂商 capex 上调提供分子端支撑，
但反转需要美债配合，<b>只适合小仓位试错</b></td></tr>
<tr><td><span class="tag t-lead">主升 LEADING</span></td><td><b>国防军工 / 信创 / 稀土</b></td>
<td>对抗因子加成方向。8/5 中方五项反制与 8/7 美方禁钨废料出口，
都在强化「自主可控 + 管制筹码」的定价逻辑。<b>这一格是加入中美变量后新增的</b></td></tr>
<tr><td><span class="tag t-emg">启动 EMERGING</span></td><td>电网设备、核电、光伏、石化化工、工程机械</td>
<td>8/3 资金转向的去处；中金认为部分周期行业供需格局边际改善</td></tr>
<tr><td><span class="tag t-fade">退潮 FADING</span></td><td><b>银行、白酒、煤炭、油气、保险</b></td>
<td>7 月避险资金的集中去处，涨幅已兑现；8/7 铁路公路、多元金融跌幅居前是资金开始撤离的早期信号。
<b>这是本轮最容易站错边的位置</b>——它们仍会排在所有涨幅榜前列</td></tr>
</tbody></table>
<div class="note">象限定义见 <code>docs/strategy_macro_regime.md</code>：
水平 = RPS_60（中期强度），方向 = RPS_20 − RPS_60（加速度）。
白名单只收 LEADING 与 EMERGING，明确排除 FADING。</div></div>

<h2>十、关键时间窗口</h2>
<div class="card"><table><thead><tr><th style="width:110px">时间</th><th style="width:290px">事件</th>
<th>为什么重要</th></tr></thead><tbody>{cal_rows}</tbody></table></div>

<h2>十一、四条最需要记住的</h2>
<div class="card">
<p style="margin-top:0"><b>1. 盯两个变量，不要盯上证指数。</b>
分母端看 30Y 美债（5.00% / 5.45% 是两道分界），分子端看中美清单动作。
指数是这两个变量的结果，不是原因。</p>
<p><b>2. 「结构性修复」不等于「可以满仓」。</b>
8 月 7 日赚钱效应只有 51%，涨幅集中在两个方向。这种盘面下扩大持仓数量只会稀释收益，
应该集中在 LEADING 象限的少数标的上。</p>
<p><b>3. 7 月涨得最好的板块，是 8 月最危险的位置。</b>
银行、白酒、煤炭在 7 月吸纳了全部避险资金，它们会长期霸占涨幅榜前列，但资金已开始撤离。
这正是 RPS 类策略最容易亏钱的「退潮象限」。</p>
<p style="margin-bottom:0"><b>4. 9 月会晤前降敞口，不是因为看空，而是因为方差。</b>
会晤时间已知、结果未知。这种情况下期望收益不变但方差骤增，
降仓是唯一不需要预测就能做对的动作。<code>macro/geopolitics.py</code>
已把这条规则写死成会晤前 7 个交易日仓位封顶 60%。</p>
</div>

<h2>数据来源</h2>
<div class="card src"><ol>{src}</ol>
<div class="note" style="margin-top:14px">
<b>关于数据可靠性的说明：</b>本报告的指数点位、板块涨跌幅、资金流数据均来自上述公开报道，
未经交易所原始数据核对。文中已逐处标注推算值与估算值。
7 月板块涨跌幅采用「近 20 个交易日」口径，与自然月口径可能存在差异。
本报告为信息整理与情景推演，不构成投资建议。</div></div>

</body></html>"""


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True, parents=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"已生成 {OUT.resolve()}  ({OUT.stat().st_size/1024:.0f} KB)")
