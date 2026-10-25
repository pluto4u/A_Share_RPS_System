"""
================================================================================
中美产业链结构变化 + 大资金配置框架  报告生成器
================================================================================
承接 tools/build_market_review_202608.py，聚焦两个问题：
    1. 政策的边际变化把产业链推向了什么形态
    2. 在容量、回撤、持有期约束下，大资金的配置逻辑与散户有何本质不同

单文件 HTML，纯内联 SVG，零依赖。

用法: python tools/build_supply_chain_positioning.py
输出: output/supply_chain_positioning_202608.html
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

OUT = Path("output/supply_chain_positioning_202608.html")

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',"
        "'PingFang SC',sans-serif")

US, CN = "#b91c1c", "#1d4ed8"


def esc(s):
    return html.escape(str(s))


# =============================================================================
# 数据
# =============================================================================

# 工具箱演化：从价格工具 → 准入工具
TOOLBOX = [
    ("美方", "1260H 涉军企业名单", "国防部", "声誉 + 政府采购限制",
     "6/8 扩容至阿里、比亚迪、宇树、百度、京东方、药明康德、蔚来等民用科技企业",
     "低", US),
    ("美方", "UFLPA 涉疆实体清单", "国土安全部", "举证责任倒置（有罪推定）",
     "7/31 单次新增 43 家，总数 187 家，为 2022 年以来最大扩容",
     "中", US),
    ("美方", "301 / 232 条款", "USTR / 商务部", "关税（价格工具）",
     "7/23 对 60 经济体加税，中国适用 12.5%；7/9 启动飞机与发动机 232 调查",
     "中", US),
    ("美方", "1286 科研机构清单", "国防部", "研发与人才链",
     "7/23 列入中俄伊 130 所高校院所，含复旦、上海交大",
     "中", US),
    ("美方", "<b>FCC 设备认证</b>", "联邦通信委员会", "<b>市场准入（二元）</b>",
     "7/28 禁外国先进机器人与联网逆变器获新认证；此前已覆盖无人机、路由器、"
     "海底光缆、检测实验室、电信运营；8/4 传闻延伸至光模块",
     "<b>高</b>", US),

    ("中方", "出口管制管控名单", "商务部", "两用物项断供",
     "6/22 列入美国稀土公司、杰亚机器人、蒂尔无人机等 10 家实体",
     "中", CN),
    ("中方", "反制清单", "商务部", "禁止境内主体与之交易",
     "8/5 新增应用 DNA 科学等 6 家；反制协助 FCC 的美国合规性测试公司",
     "中", CN),
    ("中方", "政府采购排除", "财政部", "需求端封堵",
     "6/22 规定政府采购不得采购 46 家美企产品（在华美资除外）",
     "低", CN),
    ("中方", "<b>产业链供应链安全调查</b>", "商务部", "<b>制度化工具</b>",
     "6/24 公布《工作办法》；8/5 落地首例「对外贸易国家安全调查」"
     "（含外国系统软件的进口打印复印设备）",
     "<b>高</b>", CN),
    ("中方", "<b>CCC 强制认证</b>", "市场监管总局", "<b>市场准入（镜像 FCC）</b>",
     "8/5 暂停 CCC 指定机构委托在美认证机构实施获证后工厂跟踪检查",
     "<b>高</b>", CN),
]

# 产业链三层结构
LAYERS = [
    dict(
        no="第一层", name="已完成地理转移", color="#0369a1",
        what="终端组装 / 模块封装",
        evidence="中际旭创马来西亚基地 800G 月产 200 万只（全球最大单体）+ 泰国一二期月产 50 万只高端；"
                 "新易盛泰国基地 800G/1.6T 月产超 40 万只。中际旭创海外收入占比 "
                 "2020 年 69.31% → 2024 年 86.81%",
        implication="「原产地」这个概念已经被洗掉了。这一层的博弈焦点因此从"
                    "「能不能造」变成「规则按原产地还是按企业国籍写」",
    ),
    dict(
        no="第二层", name="无法转移的瓶颈", color="#b45309",
        what="双向卡位",
        evidence="<b>中国卡美国</b>：InP 衬底（国内市占 60%+、2-6 英寸唯一量产、"
                 "实施出口许可证制度）、稀土、钨（8/7 美方禁钨废料出口反向印证）。"
                 "<b>美国卡中国</b>：EDA、先进制程设备、高端 EML/DSP 芯片、FCC 认证",
        implication="这一层不会转移，只会加价。持有瓶颈资产的一方在对抗升级时"
                    "获得定价权——这是「对手缺什么我就涨什么」的物质基础",
    ),
    dict(
        no="第三层", name="依赖仍在加深", color="#15803d",
        what="需求与技术生态",
        evidence="北美四大云厂商 2026 资本开支指引合计 7200-7450 亿美元、可见性延伸至 2028；"
                 "全球高速光模块绝大部分来自中国厂商；LITE 称 InP 供需缺口超 30%；"
                 "中信证券：完全替换存量光模块「代价不可想象」",
        implication="脱钩的边际成本对双方都在上升。这是压低极端情景概率的力量，"
                    "也是每次禁令传闻都「雷声大雨点小」的结构性原因",
    ),
]

# 配置四象限：准入依赖度 × 不可替代性
QUAD = {
    ("high_irrep", "low_access"): dict(
        name="核心底仓", color="#15803d", weight="20-30%",
        sectors="InP 衬底 / 稀土 / 关键矿产 / 军工 / 信创",
        logic="技术不可替代 + 不依赖美国市场。对抗升级时因管制筹码而涨价，"
              "缓和时因内需与财政加速而有托底。",
        risk="峰会缓和 + 中方放松出口许可 → 涨价逻辑削弱。"
             "<b>这一格的最大风险不是对抗，是和解。</b>",
        cap="容量偏小，多数标的日均成交额支撑不了大资金建仓",
    ),
    ("high_irrep", "high_access"): dict(
        name="波动核心", color="#b45309", weight="15-25%",
        sectors="光模块龙头 / 光器件 / CXO",
        logic="全球份额领先但对美收入占比极高。<b>这是唯一能装下大资金的 AI 硬件敞口</b>，"
              "8/4 通信设备主力净流入 221 亿居首就是容量优势的直接体现。",
        risk="FCC 规则若按企业国籍写，东南亚产能失效。但即便如此也不是归零，"
             "而是估值中枢下移 + 增速放缓（国内与非美市场仍在）。",
        cap="<b>A 股少数能容纳大资金的科技敞口</b>",
    ),
    ("low_irrep", "low_access"): dict(
        name="政策主线", color="#0369a1", weight="25-35%",
        sectors="电网设备 / 核电 / 特高压 / 内需服务 / 建筑",
        logic="7/30 政治局会议「加大逆周期调节力度」「加快财政支出和债券资金使用进度」"
              "的直接受益方向。不在任何清单上，不受准入风险影响。",
        risk="缺乏弹性，赚的是政策 beta 而非产业 alpha。财政落地不及预期则平庸。",
        cap="<b>容量最大，是大资金真正的加仓去处</b>",
    ),
    ("low_irrep", "high_access"): dict(
        name="系统性减配", color="#b91c1c", weight="0-5%",
        sectors="纺织服装 / 家电出口 / 机器人出海 / 光伏组件",
        logic="既无技术护城河，又高度依赖美国市场。UFLPA 清单扩容、FCC 机器人禁令、"
              "多晶硅关税已逐一命中。",
        risk="<b>这不是估值问题，是准入问题——没有价格能补偿「卖不进去」。</b>",
        cap="—",
    ),
}

# 大资金 vs 散户的约束差异
CONSTRAINTS = [
    ("容量", "可买任何标的", "日均成交额 × 可参与比例 × 建仓天数 = 硬上限",
     "云南锗业四连板期间对大资金<b>根本不存在买点</b>——不是「等回调」，是「与我无关」"),
    ("持有期", "可日内进出", "3-12 个月，必然跨越 9 月峰会与年底 FCC 窗口",
     "不能采用「等落地再决定」的策略，必须在不确定中持有"),
    ("仓位", "可空仓等待", "契约与考核要求始终有较高仓位",
     "择时能力被结构性削弱，<b>只能靠配置结构而非时点</b>获取超额"),
    ("回撤", "自行承担", "净值回撤线 / 相对排名压力",
     "7/17 单日 -3.05%、7/30 光通信板块单日 -12% 这类冲击必须<b>事前</b>用结构防"),
    ("信息", "依赖公开信息", "可做产业链调研、见管理层",
     "优势在验证「情景三下公司会怎样」，而不在抢先知道 FCC 何时发文"),
]

# 关键结论
KEY_POINTS = [
    dict(
        n=1, title="工具从「价格」变成「准入」，这是质变不是量变",
        color="#b91c1c",
        body="关税是价格工具——可量化、可对冲（涨价传导、汇率、转口）、可谈判。"
             "清单与认证是准入工具——二元的，要么能卖要么不能卖，<b>没有任何价格能补偿</b>。"
             "对估值方法的含义是：不再是在 DCF 里调成本，而是要给一个「准入折价」。"
             "关税时代可以算弹性，清单时代只能算概率。",
    ),
    dict(
        n=2, title="中方从「对等报复」转向「制度建设」",
        color="#1d4ed8",
        body="6/24《产业链供应链安全调查工作办法》+ 8/5 首例国安调查 + 暂停 CCC 跟踪检查——"
             "这三项都不是一次性反制，而是在建<b>常设工具</b>，且形态上镜像美方"
             "（国安调查对应 232，CCC 对应 FCC）。"
             "含义：中方在为长期做准备，而不是为一次谈判造筹码。"
             "<b>这降低了「峰会一揽子解决」的概率。</b>",
    ),
    dict(
        n=3, title="不是脱钩，是「错位互锁」",
        color="#15803d",
        body="制造地在转移（东南亚产能已成规模），技术依赖在深化"
             "（云厂商 capex 上调、InP 缺口 >30%、存量替换「代价不可想象」）。"
             "地理上分离，技术上更纠缠。<b>推论：重构的受益者不是「替代者」，"
             "而是「接口层」</b>——在两个体系之间提供转换、认证、代工、封装、"
             "以及卖设备的环节。",
    ),
    dict(
        n=4, title="对大资金而言，「买上游避成品」这个结论可能是倒过来的",
        color="#7c2d12",
        body="从纯产业逻辑看，上游（InP 衬底、光子设备）确实比模块成品更安全。"
             "但把容量约束加进来后结论会翻转：<b>上游标的普遍装不下大资金，"
             "而光模块龙头是 A 股少数能容纳大体量的 AI 硬件敞口。</b>"
             "8/4 通信设备主力净流入 221 亿能进来、上游材料进不来，就是这个约束的直接证据。"
             "散户的最优解与大资金的最优解在这里是分岔的。",
    ),
    dict(
        n=5, title="同板块内应当极度集中于龙头，而不是分散",
        color="#0369a1",
        body="这与散户的直觉相反。原因在于情景三（FCC 按企业国籍立规）下的差异："
             "有东南亚产能的龙头是<b>估值中枢下移 + 增速放缓</b>，"
             "没出海产能的二三线是<b>真的失去市场</b>。"
             "分散持有同一板块的二三线，等于把「情景三能扛」的属性稀释掉了。"
             "大资金应买的是「情景一二都赚、情景三能扛」的标的，"
             "而不是「情景一暴涨、情景三归零」的。",
    ),
]

# 双哑铃
BARBELL = [
    ("美债端（分母 / 估值）", "#7c2d12",
     "高估值成长（光模块、CRO、PCB）", "红利与公用事业（久期短、现金流对利率不敏感）",
     "对冲 30Y 美债在 5.0-5.4% 高位震荡的估值压缩风险"),
    ("对抗端（分子 / 准入）", "#1d4ed8",
     "对抗受损方（出口链、被清单板块）", "对抗受益方（InP / 稀土 / 军工 / 信创）",
     "对冲 9 月峰会与年底 FCC 窗口的二元结果"),
]

# 陷阱
TRAPS = [
    ("7 月涨最好的红利资产", "#b91c1c",
     "银行 +11.19%、白酒 +11.6%、煤炭 +14.29% 吸纳了 7 月全部避险资金。"
     "8/7 铁路公路、多元金融跌幅居前是资金开始撤离的早期信号。"
     "它们会长期霸占涨幅榜前列——<b>这正是 RPS 类策略最容易站错边的「退潮象限」</b>。"),
    ("没有真实瓶颈地位的「国产替代」标签股", "#b45309",
     "自主可控是一个标签，瓶颈地位是一个事实。"
     "判据是：这家公司缺席，对方的产线会不会停。"
     "答案是「不会，只是贵一点」的，都是标签股。"),
    ("把上游当成对抗的避风港", "#c2410c",
     "光器件厂的下游就是中国模块厂。模块厂对美出货受限，器件厂的国内订单同样掉。"
     "上游不是免疫，只是隔了一层缓冲——敏感度是 -0.3，不是 0。"),
    ("在事件窗口内加仓博方向", "#7f1d1d",
     "9 月峰会与 FCC 规则都是二元结果。"
     "在这种窗口内，期望收益不变而方差骤增，"
     "<b>加仓是在为不可预测的事情支付溢价</b>。正确动作是降敞口而非猜方向。"),
]


# =============================================================================
# SVG
# =============================================================================

def chart_toolbox(width=1000):
    """工具箱演化：按杀伤力排序"""
    rows = TOOLBOX
    pad_t, row_h = 46, 30
    height = pad_t + len(rows) * row_h + 22
    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="16" y="20" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'双方工具箱的演化 —— 从价格工具走向准入工具</text>')
    p.append(f'<text x="16" y="38" font-size="11.5" fill="#64748b">'
             f'条形长度 = 工具的「不可对冲程度」。'
             f'<tspan fill="{US}">红=美方</tspan> '
             f'<tspan fill="{CN}">蓝=中方</tspan></text>')
    lvl = {"低": 0.33, "中": 0.62, "高": 1.0, "<b>高</b>": 1.0}
    for i, (side, name, org, kind, _, power, col) in enumerate(rows):
        y = pad_t + i * row_h
        clean = name.replace("<b>", "").replace("</b>", "")
        w = lvl.get(power, 0.5) * 300
        p.append(f'<text x="16" y="{y+16}" font-size="12" fill="#334155">'
                 f'<tspan fill="{col}" font-weight="700">[{side[0]}]</tspan> {esc(clean)}</text>')
        p.append(f'<rect x="250" y="{y+4}" width="{w:.0f}" height="16" rx="2.5" '
                 f'fill="{col}" opacity="0.8"/>')
        p.append(f'<text x="{250+w+9:.0f}" y="{y+16}" font-size="11.5" fill="#475569">'
                 f'{esc(kind.replace("<b>","").replace("</b>",""))}'
                 f'<tspan fill="#94a3b8"> · {esc(org)}</tspan></text>')
    p.append('</svg>')
    return "".join(p)


def chart_interlock(width=1000, height=290):
    """错位互锁示意"""
    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="16" y="22" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'错位互锁：制造地在分离，技术依赖在加深</text>')

    boxes = [
        (30, 56, 250, 62, "#0369a1", "第一层 · 已转移", "东南亚产能已成规模"),
        (30, 138, 250, 62, "#b45309", "第二层 · 无法转移", "双向瓶颈，只会加价"),
        (30, 220, 250, 56, "#15803d", "第三层 · 依赖加深", "capex 上调，缺口 >30%"),
    ]
    for x, y, w, h, c, t1, t2 in boxes:
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{c}" '
                 f'opacity="0.13" stroke="{c}" stroke-width="1.3"/>')
        p.append(f'<text x="{x+14}" y="{y+25}" font-size="13" font-weight="700" '
                 f'fill="{c}">{esc(t1)}</text>')
        p.append(f'<text x="{x+14}" y="{y+44}" font-size="11.5" fill="#475569">{esc(t2)}</text>')

    # 右侧：中美互卡
    p.append(f'<text x="330" y="76" font-size="12.5" font-weight="700" fill="{CN}">'
             f'中国卡美国</text>')
    for i, s in enumerate(["InP 衬底（出口许可证）", "稀土加工", "钨等关键矿产",
                           "高速光模块产能"]):
        p.append(f'<text x="330" y="{98+i*20}" font-size="11.5" fill="#334155">· {esc(s)}</text>')

    p.append(f'<text x="640" y="76" font-size="12.5" font-weight="700" fill="{US}">'
             f'美国卡中国</text>')
    for i, s in enumerate(["EDA 与先进制程设备", "高端 EML / DSP 芯片",
                           "FCC 设备认证（市场准入）", "云厂商订单"]):
        p.append(f'<text x="640" y="{98+i*20}" font-size="11.5" fill="#334155">· {esc(s)}</text>')

    p.append(f'<path d="M560,150 L620,150" stroke="{CN}" stroke-width="2" '
             f'marker-end="url(#a1)"/>')
    p.append(f'<path d="M620,172 L560,172" stroke="{US}" stroke-width="2" '
             f'marker-end="url(#a2)"/>')
    p.append(f'<defs>'
             f'<marker id="a1" markerWidth="8" markerHeight="8" refX="7" refY="3" '
             f'orient="auto"><path d="M0,0 L7,3 L0,6" fill="{CN}"/></marker>'
             f'<marker id="a2" markerWidth="8" markerHeight="8" refX="7" refY="3" '
             f'orient="auto"><path d="M0,0 L7,3 L0,6" fill="{US}"/></marker></defs>')
    p.append(f'<text x="330" y="268" font-size="11.5" fill="#64748b">'
             f'两边都握着对方的命门 —— 这是压低极端情景概率的结构性力量，'
             f'也是每次禁令传闻都「雷声大雨点小」的原因</text>')
    p.append('</svg>')
    return "".join(p)


def chart_quadrant(width=1000, height=460):
    """配置四象限：准入依赖度 × 不可替代性"""
    pad_l, pad_t = 152, 78
    cw = (width - pad_l - 24) / 2
    chh = (height - pad_t - 40) / 2

    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="18" y="22" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'配置四象限：不可替代性 × 准入依赖度</text>')
    p.append(f'<text x="18" y="40" font-size="11.5" fill="#64748b">'
             f'方块内为示例权重区间，不是建议配比；实际比例取决于产品契约、'
             f'考核口径与风险预算</text>')

    cols = [("low_access", "低准入依赖", "对美收入占比低"),
            ("high_access", "高准入依赖", "对美收入占比高")]
    rows = [("high_irrep", "高不可替代", "缺席则对方停产"),
            ("low_irrep", "低不可替代", "缺席则对方涨价")]

    for j, (_, cn, cd) in enumerate(cols):
        x = pad_l + j * cw
        p.append(f'<text x="{x+cw/2:.0f}" y="{pad_t-26}" text-anchor="middle" '
                 f'font-size="13" font-weight="700" fill="#1e293b">{esc(cn)}</text>')
        p.append(f'<text x="{x+cw/2:.0f}" y="{pad_t-9}" text-anchor="middle" '
                 f'font-size="10.5" fill="#94a3b8">{esc(cd)}</text>')

    for i, (rk, rn, rd) in enumerate(rows):
        y = pad_t + i * chh
        p.append(f'<text x="{pad_l-14}" y="{y+chh/2-4:.0f}" text-anchor="end" '
                 f'font-size="13" font-weight="700" fill="#1e293b">{esc(rn)}</text>')
        p.append(f'<text x="{pad_l-14}" y="{y+chh/2+14:.0f}" text-anchor="end" '
                 f'font-size="10.5" fill="#94a3b8">{esc(rd)}</text>')
        for j, (ck, _, _) in enumerate(cols):
            q = QUAD[(rk, ck)]
            x = pad_l + j * cw
            p.append(f'<rect x="{x+4:.0f}" y="{y+4:.0f}" width="{cw-8:.0f}" '
                     f'height="{chh-8:.0f}" rx="8" fill="{q["color"]}" opacity="0.13" '
                     f'stroke="{q["color"]}" stroke-width="1.2"/>')
            p.append(f'<text x="{x+20:.0f}" y="{y+32:.0f}" font-size="15.5" '
                     f'font-weight="700" fill="{q["color"]}">{esc(q["name"])}</text>')
            p.append(f'<text x="{x+cw-24:.0f}" y="{y+32:.0f}" text-anchor="end" '
                     f'font-size="14" font-weight="700" fill="{q["color"]}">'
                     f'{esc(q["weight"])}</text>')
            sec = q["sectors"]
            # 换行
            parts_ = sec.split(" / ")
            line, lines = "", []
            for w_ in parts_:
                if len(line) + len(w_) > 22:
                    lines.append(line.strip(" /"))
                    line = w_ + " / "
                else:
                    line += w_ + " / "
            lines.append(line.strip(" /"))
            for k, ln in enumerate(lines[:3]):
                p.append(f'<text x="{x+20:.0f}" y="{y+56+k*18:.0f}" font-size="12" '
                         f'fill="#334155">{esc(ln)}</text>')
    p.append('</svg>')
    return "".join(p)


def chart_barbell(width=1000, height=250):
    """双哑铃"""
    p = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="font-family:{FONT}">']
    p.append(f'<text x="18" y="22" font-size="13.5" font-weight="700" fill="#1e293b">'
             f'双哑铃：两个变量各配一对</text>')
    p.append(f'<text x="18" y="40" font-size="11.5" fill="#64748b">'
             f'单哑铃只能对冲一个轴。既然有美债与对抗两个正交变量，就需要两组对冲</text>')

    for i, (axis, col, left, right, note) in enumerate(BARBELL):
        y = 62 + i * 92
        p.append(f'<text x="18" y="{y+16}" font-size="12.5" font-weight="700" '
                 f'fill="{col}">{esc(axis)}</text>')
        p.append(f'<rect x="18" y="{y+26}" width="380" height="38" rx="7" '
                 f'fill="{col}" opacity="0.13" stroke="{col}" stroke-width="1.1"/>')
        p.append(f'<text x="208" y="{y+50}" text-anchor="middle" font-size="12.5" '
                 f'fill="#1e293b">{esc(left)}</text>')
        p.append(f'<rect x="470" y="{y+26}" width="380" height="38" rx="7" '
                 f'fill="{col}" opacity="0.13" stroke="{col}" stroke-width="1.1"/>')
        p.append(f'<text x="660" y="{y+50}" text-anchor="middle" font-size="12.5" '
                 f'fill="#1e293b">{esc(right)}</text>')
        p.append(f'<line x1="398" y1="{y+45}" x2="470" y2="{y+45}" stroke="{col}" '
                 f'stroke-width="2.6"/>')
        p.append(f'<circle cx="434" cy="{y+45}" r="9" fill="#fff" stroke="{col}" '
                 f'stroke-width="2"/>')
        p.append(f'<text x="866" y="{y+50}" font-size="11" fill="#64748b">{esc(note[:22])}</text>')
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
h2{font-size:17px;margin:36px 0 12px;padding-bottom:9px;border-bottom:2px solid #dde4ec}
h3{font-size:14.5px;margin:22px 0 8px;color:#334155}
.sub{color:#64748b;font-size:13px;margin-bottom:24px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:11px;padding:18px 22px;
      margin-bottom:16px}
.lead{background:#0f172a;color:#fff;border-radius:11px;padding:22px 26px;margin-bottom:20px}
.lead b{color:#fbbf24}
.lead .h{font-size:17px;font-weight:650;margin-bottom:8px}
.lead p{margin:0 0 10px;opacity:.94;font-size:14px}
.lead p:last-child{margin-bottom:0}
table{width:100%%;border-collapse:collapse;font-size:13px;margin:6px 0}
th{background:#f1f5f9;text-align:left;padding:9px 11px;font-weight:600;color:#475569;
   border-bottom:1px solid #e2e8f0}
td{padding:8px 11px;border-bottom:1px solid #f1f5f9;vertical-align:top}
tr:hover td{background:#fafcfe}
.kp{border-left:5px solid;border-radius:9px;background:#fff;border-top:1px solid #e2e8f0;
    border-right:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;padding:16px 21px;
    margin-bottom:14px}
.kp .t{font-size:15.5px;font-weight:660;margin-bottom:6px}
.kp .n{display:inline-block;width:24px;height:24px;border-radius:50%%;color:#fff;
       text-align:center;line-height:24px;font-size:13px;font-weight:700;margin-right:9px}
.warn{background:#fffbeb;border:1px solid #fde68a;color:#78350f;padding:13px 17px;
      border-radius:9px;margin:14px 0;font-size:13.5px}
.danger{background:#fef2f2;border:1px solid #fecaca;color:#991b1b;padding:13px 17px;
        border-radius:9px;margin:14px 0;font-size:13.5px}
.note{color:#64748b;font-size:12.5px;margin-top:9px}
.layer{border-left:5px solid;background:#fff;border-radius:9px;padding:16px 21px;
       margin-bottom:13px;border-top:1px solid #e2e8f0;border-right:1px solid #e2e8f0;
       border-bottom:1px solid #e2e8f0}
.layer .h{font-size:15px;font-weight:650;margin-bottom:5px}
.layer dl{margin:8px 0 0;display:grid;grid-template-columns:66px 1fr;gap:5px 14px;
          font-size:13.5px}
.layer dt{color:#64748b;font-weight:600}
.layer dd{margin:0}
.disc{background:#f1f5f9;border-radius:9px;padding:15px 19px;font-size:12.5px;
       color:#475569;margin-top:30px}
""" % FONT


def build():
    tb_rows = "".join(
        f'<tr><td style="color:{c};font-weight:700;white-space:nowrap">{esc(side)}</td>'
        f'<td><b>{name}</b></td><td style="color:#64748b;white-space:nowrap">{esc(org)}</td>'
        f'<td>{kind}</td><td style="color:#475569">{detail}</td>'
        f'<td style="text-align:center">{power}</td></tr>'
        for side, name, org, kind, detail, power, c in TOOLBOX
    )

    layers = "".join(
        f'<div class="layer" style="border-left-color:{L["color"]}">'
        f'<div class="h" style="color:{L["color"]}">{esc(L["no"])} · {esc(L["name"])}'
        f'<span style="color:#94a3b8;font-weight:400;font-size:13px">　{esc(L["what"])}</span></div>'
        f'<dl><dt>证据</dt><dd>{L["evidence"]}</dd>'
        f'<dt>含义</dt><dd><b>{L["implication"]}</b></dd></dl></div>'
        for L in LAYERS
    )

    kps = "".join(
        f'<div class="kp" style="border-left-color:{k["color"]}">'
        f'<div class="t"><span class="n" style="background:{k["color"]}">{k["n"]}</span>'
        f'{esc(k["title"])}</div><div>{k["body"]}</div></div>'
        for k in KEY_POINTS
    )

    cons = "".join(
        f'<tr><td><b>{esc(a)}</b></td><td style="color:#94a3b8">{esc(b)}</td>'
        f'<td>{esc(c)}</td><td style="color:#475569">{d}</td></tr>'
        for a, b, c, d in CONSTRAINTS
    )

    quads = ""
    for rk in ("high_irrep", "low_irrep"):
        for ck in ("low_access", "high_access"):
            q = QUAD[(rk, ck)]
            quads += (
                f'<div class="layer" style="border-left-color:{q["color"]}">'
                f'<div class="h" style="color:{q["color"]}">{esc(q["name"])}'
                f'<span style="float:right;font-size:14px">{esc(q["weight"])}</span></div>'
                f'<dl><dt>方向</dt><dd>{esc(q["sectors"])}</dd>'
                f'<dt>逻辑</dt><dd>{q["logic"]}</dd>'
                f'<dt>风险</dt><dd>{q["risk"]}</dd>'
                f'<dt>容量</dt><dd>{q["cap"]}</dd></dl></div>'
            )

    traps = "".join(
        f'<div class="danger" style="margin:0 0 11px"><b>{esc(t)}</b><br>{d}</div>'
        for t, c, d in TRAPS
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中美产业链结构变化与大资金配置框架</title><style>{CSS}</style></head><body>

<h1>中美产业链结构变化与大资金配置框架</h1>
<div class="sub">信息截至 2026-08-08 · 生成于 {datetime.now():%Y-%m-%d %H:%M} ·
承接《A股 2026年7月-8月首周复盘与修复路径推演》</div>

<div class="lead">
  <div class="h">两句话结论</div>
  <p><b>产业链没有在脱钩，而是在「错位互锁」</b>——制造地已经转移（中际旭创马来西亚月产
  200 万只 800G、泰国 50 万只，海外收入占比 86.81%），但技术依赖同时在加深
  （云厂商 capex 上调至 7200-7450 亿美元、InP 缺口 >30%）。地理上分离，技术上更纠缠。</p>
  <p>真正的边际变化不在摩擦强度，而在<b>工具性质</b>：双方都从「价格工具」转向「准入工具」。
  美方用 FCC 认证，中方用 CCC 与国安调查。<b>关税可以对冲，准入不能</b>——
  这从根本上改变了估值方法：不再是在 DCF 里调成本，而是要给一个准入折价。</p>
</div>

<h2>一、政策工具箱的演化</h2>
<div class="card">{chart_toolbox()}</div>
<div class="card"><table><thead><tr><th style="width:48px">方</th>
<th style="width:180px">工具</th><th style="width:110px">主管</th>
<th style="width:150px">性质</th><th>近期动作</th><th style="width:60px">杀伤力</th>
</tr></thead><tbody>{tb_rows}</tbody></table>
<div class="warn"><b>FCC 这条线是边际变化最大的。</b>
它绕开了传统制裁的法律程序（不需要把国家安全威胁证明到证据标准），
用监管认证权直接封堵市场准入。商务部自己列举的清单已覆盖
<b>电信运营、检测实验室、无人机、消费级路由器、海底光缆</b>，
7/28 又加上先进机器人与电力逆变器，8/4 传闻延伸至光模块。
含义：<b>制裁的边际成本对美方在下降，所以频率会上升。</b></div>
<div class="warn" style="background:#eff6ff;border-color:#bfdbfe;color:#1e3a8a">
<b>中方的镜像动作同样值得注意。</b>
8/5 五项反制里最有信息量的是后两项——首例「对外贸易国家安全调查」对应美方 232 条款，
暂停 CCC 在美机构工厂检查对应 FCC 认证权。
加上 6/24《产业链供应链安全调查工作办法》，<b>这三项都不是一次性反制，而是在建常设工具</b>。</div></div>

<h2>二、产业链的真实形态：错位互锁</h2>
<div class="card">{chart_interlock()}</div>
{layers}

<h2>三、五个判断</h2>
{kps}

<h2>四、大资金的约束条件</h2>
<div class="card"><table><thead><tr><th style="width:70px">维度</th>
<th style="width:150px">散户</th><th style="width:250px">大资金</th><th>含义</th>
</tr></thead><tbody>{cons}</tbody></table>
<div class="note">这张表是理解「为什么同一份研究会导出不同结论」的关键。
产业逻辑是共享的，约束条件不是。</div></div>

<h2>五、配置框架</h2>
<div class="card">{chart_quadrant()}
<div class="note">两个维度都是<b>可证伪</b>的：不可替代性问「这家公司缺席，对方的产线会不会停」；
准入依赖度问「对美收入占比 × 是否在任何清单上 × 品类是否被 FCC 盯上」。</div></div>

{quads}

<h2>六、双哑铃对冲</h2>
<div class="card">{chart_barbell()}
<div class="note">常见做法是只做一组哑铃（成长 vs 红利），
但那只对冲了美债这一个轴。既然对抗是正交的第二个变量，就需要第二组对冲，
否则在「美债缓和 + 对抗升级」这一格里会两头挨打。</div></div>

<h2>七、当前因子给出的一个可操作结论</h2>
<div class="card">
<p style="margin-top:0"><code>macro/geopolitics.py</code> 用现有事件日志跑出来的读数是
<b>G1 事件强度 94、G2 市场隐含溢价 57，背离 37 分</b>。</p>
<p>G1 度量客观发生了什么，G2 度量市场已经定价了多少（自主可控篮子 − 出口链篮子的 20 日收益差）。
<b>G1 显著高于 G2 意味着：这一轮从 6 月开始的对抗升级，市场还没有充分定价。</b></p>
<p style="margin-bottom:0">推论：<b>「减出口敞口、加自主可控」这个交易还没做完。</b>
反过来说，如果哪天 G2 追上甚至超过 G1，那就是这个交易拥挤、该反向布局的信号——
特别是在峰会缓和的情景下，被过度定价的受损板块会有修复弹性。
这是一个可以每天跑出来的读数，不需要依赖任何观点。</p></div>

<h2>八、四个陷阱</h2>
{traps}

<h2>九、需要盯的分水岭</h2>
<div class="card"><table><thead><tr><th style="width:200px">观测项</th>
<th style="width:180px">分水岭</th><th>为什么重要</th></tr></thead><tbody>
<tr><td><b>FCC 是否发布 NPRM</b></td><td>从传闻到程序启动</td>
<td>最重要的单一信号。事件日志里 8/4 传闻的 severity 目前只给 2，NPRM 一旦发布应升到 4</td></tr>
<tr><td><b>FCC 规则的措辞</b></td><td>country of origin<br>vs. entity nationality</td>
<td>决定东南亚产能是否有效。7/28 机器人禁令用的是「无论其原产国国籍为何」+
只给 DoD/DHS 批准者豁免——这个模板如果复用，产能出海就不管用</td></tr>
<tr><td>9 月峰会公报</td><td>是否提及「减少不确定性的出口限制措施」</td>
<td>判断是战术性缓和还是仅象征性成果</td></tr>
<tr><td>中方 InP 出口许可</td><td>收紧 / 放松</td>
<td>收紧 = 对称反制升级；<b>放松 = 稀土与衬底的涨价逻辑瓦解</b>。
注意这一格的最大风险是和解不是对抗</td></tr>
<tr><td>关税休战到期安排</td><td>是否延长</td>
<td>一年期休战接近到期，延长是双方成本最低的交易，也是最可能的成果形态</td></tr>
<tr><td>G2 市场隐含溢价</td><td>是否追上 G1</td>
<td>拥挤度信号。追上即意味着对抗交易已充分定价</td></tr>
</tbody></table></div>

<div class="disc">
<b>说明与免责</b>：本文是基于公开信息的结构分析与配置<b>框架</b>，不是投资建议，
也不构成对任何具体标的的推荐。文中的权重区间仅为说明框架逻辑的示例，
实际配置取决于产品契约、考核口径、风险预算与流动性约束，需由投资者自行判断。
我不是投资顾问。<br><br>
所有事实性内容的来源见《A股 2026年7月-8月首周复盘与修复路径推演》
（<code>output/market_review_202608.html</code>）的数据来源章节，
以及 <code>macro/geo_events.json</code> 中逐条标注的事件日志。
其中 8/4 FCC 光模块禁令为路透社援引消息人士的<b>传闻</b>，
中际旭创已回应「FCC 尚未出台相关领域限制性文件」，不应作为既成事实使用。
</div>

</body></html>"""


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True, parents=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"已生成 {OUT.resolve()}  ({OUT.stat().st_size/1024:.0f} KB)")
