"""Reproducible local five-stock PCB-chain review; price data remain local."""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import html
import json
import struct
from datetime import datetime
from pathlib import Path

import pandas as pd
from run_right_side_research import ROOT, chart
from run_optical_overlay import anchor, gaps
from run_tdx_formula_audit import formula


ITEMS = {
    '688183.SH': ('生益电子', '中游：PCB制造', 'https://www.sye.com.cn/index.aspx',
        '结构相对领先，但当日走弱',
        'MA20仅略高于MA60，刚形成的排列并不稳固。当天跌近4.8%，收盘处于振幅底部约10%，量比1.12并非缩量回踩。不能把这根阴线解释成已完成洗盘。'),
    '002916.SZ': ('深南电路', '中游：PCB、封装基板及电子装联', 'https://www.scc.com.cn/scc/cpjs/index.html',
        '当天修复领先，中期仍待转向',
        '当天涨约4.8%，量比1.62，越过此前5日高点并站回MA60，是本组较清晰的修复信号。但MA20、MA60斜率仍为负，且MA20低于MA60；收盘仅位于当日振幅中部，尚未突破20日前高。'),
    '002463.SZ': ('沪电股份', '中游：PCB制造', 'https://eniu.com/gu/sz002463/zl',
        '冲高回落，等待站稳MA60',
        '虽然相对前收上涨，但收盘低于开盘，收在振幅底部约17%。盘中越过此前20日高点，收盘未站住，且仍略低于MA60；放量不等于有效突破。'),
    '600183.SH': ('生益科技', '上游材料：覆铜板、半固化片', 'https://www.syst.com.cn/cn/jtjs/index_29.aspx',
        '价格已修复，均线排列滞后',
        '收盘站上MA20和MA60，量比0.93，尚不是放量确认。MA20仍低于MA60，MA60继续下行；RPS120很高但RPS50偏低，是长期强股的中期整理，不能只看长周期排名。'),
    '688813.SH': ('泰金新能', '上游设备：铜箔生产设备、电极', 'https://www.tjneglobal.com/zh-cn/copper-foil-equipment',
        '弱势反弹、历史不足，末位观察',
        '不直接生产PCB，主要映射铜箔厂扩产设备需求，不能等同覆铜板或PCB订单。收盘在MA20之上但显著低于下行的MA60，量比1.55却收在振幅下部，反弹质量仍弱。仅111根本地日线，不能计算完整RPS120/250。'),
}


def main():
    out = ROOT/'output'
    stamp = '20260908'
    target = pd.Timestamp('2026-09-08')
    gate = json.loads((out/'qfq_verification.json').read_text(encoding='utf-8-sig'))
    if gate['price_adjustment'] != 'verified_qfq' or gate['full_market_checks']['latest_anchor']['max_date'] != str(target.date()):
        raise ValueError('Rebuild this date-specific review after refreshing the local gate')
    allstocks = pd.read_csv(out/f'right_side_{stamp}_all.csv')
    s = allstocks.set_index('ts_code').loc[list(ITEMS)].reset_index()
    if not (pd.to_datetime(s.trade_date) == target).all():
        raise ValueError('Mixed snapshot dates')
    d = pd.read_parquet(out/f'right_side_{stamp}_history.parquet', filters=[('ts_code', 'in', list(ITEMS))])
    receipts = []
    for code in ITEMS:
        receipt = anchor(code, stamp)
        if receipt['anchor_status'] != '已读取':
            raise ValueError(f'Missing raw anchor: {code}')
        raw = Path(receipt['source']).read_bytes()
        receipt['first_local_date'] = str(struct.unpack_from('<I', raw, 0)[0])
        receipt['last_local_date'] = str(struct.unpack_from('<I', raw, len(raw)-32)[0])
        if receipt['last_local_date'] != stamp:
            raise ValueError('Newer local prices available; refresh analysis')
        row = s[s.ts_code == code].iloc[0]
        receipt['max_ohlc_diff'] = max(abs(row[k]-receipt['raw_'+k]) for k in ['open','high','low','close'])
        if receipt['max_ohlc_diff'] >= .011:
            raise ValueError('TDX and adjusted latest anchor disagree')
        receipts.append(receipt)
    pd.DataFrame(receipts).to_csv(out/f'pcb_focus_{stamp}_anchors.csv',index=False,encoding='utf-8-sig')
    f = formula(d.copy())
    f = f[f.trade_date == target].set_index('ts_code')
    s['original_formula'] = s.ts_code.map(lambda c: '/'.join(k for k in ['YJ','QD','QR','QQR'] if f.loc[c,k]) if f.loc[c,'XG'] else '未命中')
    s['chain'] = s.ts_code.map(lambda c: ITEMS[c][1])
    s['judgment'] = s.ts_code.map(lambda c: ITEMS[c][3])
    s['gaps'] = s.apply(gaps,axis=1)
    s['change_pct'] = s.ret1*100
    s['ret5_pct'] = s.ret5*100
    s['ret20_pct'] = s.ret20*100
    s['atr_pct'] = s.atr20/s.close*100
    s['range_close_pct'] = s.clv*100
    s['first_pressure'] = s[['high','ph5']].max(axis=1)
    s['breakout_reference'] = s[['high','ph20']].max(axis=1)
    s['prior5_low'] = s.pl5
    s.to_csv(out/f'pcb_focus_{stamp}.csv',index=False,encoding='utf-8-sig')
    def table(df, cols):
        return '<div class="table">'+df[[c for c,_ in cols]].rename(columns=dict(cols)).to_html(index=False,border=0,float_format=lambda x:f'{x:.2f}',na_rep='历史不足',escape=True)+'</div>'
    css = 'body{font:16px system-ui,"Microsoft YaHei";line-height:1.7;letter-spacing:0;margin:0;color:#202a30;background:#f5f7f8}header{padding:24px;background:#193d36;color:white}main{max-width:1280px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:22px}h3{font-size:18px}article{border-top:2px solid #bbc9c4;padding:18px 0}a{color:#166557}.table{overflow:auto}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td,th{padding:10px;border-bottom:1px solid #dbe0e3;white-space:nowrap;text-align:right}th{background:#e4ecea}svg{width:100%;height:auto;background:white}nav a{margin-right:18px}p{max-width:1100px}@media(max-width:600px){main{padding:12px}header{padding:16px}h1{font-size:23px}}@media print{article{break-inside:avoid}.table{overflow:visible}table{font-size:9px}}'
    page = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PCB产业链五股 · 20260908</title><style>{css}</style></head><body><header><h1>PCB产业链五股：趋势与量价复核</h1><p>行情截至2026-09-08收盘，供9月9日观察。“护电股份”按沪电股份002463核对。</p><p>当前动作：五只均未形成本轮严格筛选的直接买点；生益电子看结构，深南电路看修复，泰金新能暂不列入成熟趋势候选。</p><p>行情与指标来自本地；公司网站仅核实产业环节。缺少账户、分钟行情及逐股最新公告核查，不给出实际下单仓位。</p></header><main><nav><a href="#compare">横向比较</a><a href="#chain">产业链</a><a href="#details">逐股形态</a><a href="#method">口径与复盘</a></nav>']
    page += ['<h2 id="compare">结论：强股整理，不等于新一轮主升已确认</h2><p>四只成熟股票RPS120均高于94，但RPS50都低于35，显示过去120日强、近50日相对弱。生益电子是五只中唯一同时满足收盘&gt;MA20&gt;MA60且两线向上的股票，却未出现合格突破或回踩转强。深南电路当天量价修复最清楚，但均线尚未完成转向。不要把结构优先级、当天涨幅和盈利预期混成一个排名。</p>', table(s,[('name','股票'),('ts_code','代码'),('close','收盘元'),('change_pct','当日涨跌%'),('RPS_50','RPS50'),('RPS_120','RPS120'),('vr','量比'),('judgment','判断')])]
    industry = pd.read_csv(out/f'right_side_{stamp}_industries.csv')
    comp = industry[industry.industry.isin(s.industry) | industry.industry.isin(industry.head(3).industry)]
    page += ['<h2>行业背景不支持仅凭PCB标签优先买入</h2><p>本地通达信把四只成熟股票归入“元器件”，泰金新能归入“工业机械”；这是宽行业，不是精准PCB指数。全市场行业共振排序中元器件为第59位、工业机械第67位，当前并非排名前列。下表保留行业代码防止同名细分混淆。五只自选股不是完整行业样本，不能据此声称整个PCB板块有共振。</p>', table(comp,[('industry','行业代码'),('name','行业'),('members','样本数'),('ret5_pct','5日中位涨跌%'),('breadth_ma20_pct','MA20宽度%'),('median_rps120','RPS120中位'),('industry_rank','共振排名')])]
    page += ['<h2 id="chain">产业链摆放</h2><p>铜箔生产设备（泰金新能） → 铜箔等原材料 → 覆铜板/半固化片（生益科技） → PCB制造（生益电子、沪电股份、深南电路） → 服务器、网络设备、汽车电子等终端。深南电路还包括封装基板和电子装联。箭头表达生产环节，不代表已核实这五家公司逐一互相供货。</p><p>泰金新能是上游设备，不应直接套用PCB制造商利润弹性。其高端电子电路铜箔设备需求、验证进展及订单兑现需要另查，不能由下游景气直接推导。</p>']
    for code, info in ITEMS.items():
        page.append(f'<p>{html.escape(info[0])}：{html.escape(info[1])}。<a href="{html.escape(info[2])}">业务来源</a>。</p>')
    page += ['<h2 id="details">逐股形态与观察位</h2><p>下列价位均为截至9月8日的前复权观察位，不是自动买入指令或已验证的硬止损。均线会随交易日变化；突破价格仍须配合收盘保持、合理乖离、后续量能和同组反馈。</p>', table(s,[('name','股票'),('ma20','MA20'),('ma60','MA60'),('first_pressure','近端压力'),('breakout_reference','20日前高/当日高'),('prior5_low','前5日低点'),('atr_pct','ATR/收盘%'),('original_formula','原ZZ1234')])]
    for _,r in s.iterrows():
        code = r.ts_code
        h = d[d.ts_code == code]
        previous = h[h.trade_date < target].tail(5)
        page += [f'<article><h3>{html.escape(r["name"])} {code} · {html.escape(r.judgment)}</h3><p><strong>事实与解读：</strong>{ITEMS[code][4]}</p>',chart(h),f'<p>图为最近80个交易日前复权K线；金色MA20、蓝色MA60，成交量与价格同轴日期。5日涨跌 {r.ret5_pct:+.2f}%，20日涨跌 {r.ret20_pct:+.2f}%；距MA20 {r.vs_ma20_pct:+.2f}%，ATR20 {r.atr20:.2f}元。当前未满足项：{html.escape(r.gaps)}。</p>']
        if code == '688183.SH':
            text = '优先等缩量回踩后再转强，而不是追当天反抽。116.34至116.43附近为当日MA60/MA20重叠区，但正常波动较大，触及并不保证支撑有效；133.80为近端压力，138.47为20日前高，后者距当日MA20已约19%，即使越过也需重新评估乖离。'
        elif code == '002916.SZ':
            text = '先看能否持续守住377.95附近MA60，再看393.77及402.88压力能否化解。跌回MA60下方意味着此次收复失败风险增加；370.01是当日低点，360.83为MA20参考。不能因为一天放量阳线就假定MA60已转升。'
        elif code == '002463.SZ':
            text = '先看125.09附近MA60能否真正站稳，再看129.25当日高点能否有效收复。124.00是当日低点、119.74为MA20参考。若再次冲高收低，继续等，不把盘中越过前高当作收盘突破。'
        elif code == '600183.SH':
            text = '142.98附近MA60和139.32附近MA20是结构观察区；先看152.41近端压力，再看157.28前高。需要改善MA60斜率和成交量，而不是仅依靠长期RPS高就加仓。'
        else:
            text = '114.50为MA20参考，129.90至129.98附近为近端压力区，140.19附近下行MA60仍在上方。当前属于反弹研究，不纳入本轮成熟趋势买点；RPS50仅2.66，不能用近5日反弹掩盖中期相对弱势。'
        page.append(f'<p><strong>观察假设：</strong>{text}</p></article>')
    page += ['<h2 id="method">风险、口径与可持续复盘</h2><p>空仓：当前只观察，等待形态触发；已有仓：以各股原有交易计划、可卖数量及上述结构变化复核，不因本次未入选就机械清仓。新买当日受T+1限制，跳空或跌停可能使止损无法按计划成交。多只PCB相关股票共享产业风险，不等于分散投资。</p><p>宏观状态、盘面过程与实际账户仓位上限：本次未重新判定；已有全市场报告只作为背景，不以情绪标签替代五股的价格结构。未取得分钟承接、资金流及逐股最新财报/公告复核，本报告不包含估值结论、业绩上调或收益保证。</p><p>行情采用9月8日同口径快照；五只原始OHLC逐只核对通达信.day，最新归一前复权价格误差均小于0.011元。MA、ATR、收益、RPS使用经校验的本地等比前复权库，量额保持原始口径；verification_scope=本地运营一致性验证，非独立外部证明。量比=当天量/此前20个交易日均量，不含当天；RPS为对应窗口收益的沪深可用样本百分位，不是上涨概率。</p><p>泰金新能111根记录仅表示本地有效日线数；RPS120、RPS250与MA120保持缺失，不填零、不以短周期替代，也未据本地首条记录宣称官方上市日期。其他股票360根为本次计算窗口，不是上市年限。</p><p>持续复盘保留每日同口径CSV、价格核对凭据与报告，记录观察位收复、形态变化、+1/+5/+10/+20交易日收益和最大不利波动。冻结本次条件，不用未来数据反写判断；这套规则尚未完成样本外胜率检验。</p><p><a href="a_share_analysis_20260908_right_side_optical.html">同日全市场形态对照</a> · <a href="pcb_focus_20260908.csv">五股指标留档</a></p>',f'<p>生成于北京时间 {datetime.now():%Y-%m-%d %H:%M:%S}。</p></main></body></html>']
    path = out/f'a_share_analysis_{stamp}_pcb_focus.html'
    path.write_text(''.join(page),encoding='utf8')
    (out/f'pcb_focus_{stamp}_sources.json').write_text(json.dumps({'date':str(target.date()),'gate':gate,'business_sources':{c: i[2] for c,i in ITEMS.items()},'notes':'Web used only for business classification, not replacement market prices.'},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'report':str(path),'stocks':s[['name','original_formula','gaps']].to_dict('records'),'anchors':len(receipts)},ensure_ascii=False))


if __name__ == '__main__':
    main()
