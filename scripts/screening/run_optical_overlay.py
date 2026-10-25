"""Date-stamped optical catalyst overlay on the local shape-first screen."""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
import html
import json
import struct
from datetime import datetime
from pathlib import Path

import pandas as pd
from run_right_side_research import ROOT, chart
from run_tdx_formula_audit import formula

MAPPING = {
    '601869.SH': ('光纤光缆', 'GLW：高密度光纤需求'),
    '600487.SH': ('光纤光缆', 'GLW：高密度光纤需求'),
    '600522.SH': ('光纤光缆', 'GLW：高密度光纤需求'),
    '688498.SH': ('上游光芯片', 'LITE/COHR：激光器与光源需求'),
    '688048.SH': ('上游光芯片', 'LITE/COHR：光源技术，产品结构非等同'),
    '300394.SZ': ('光器件', 'LITE/COHR：光引擎与精密器件'),
    '300570.SZ': ('光连接器', 'GLW：高密度连接需求'),
    '300620.SZ': ('光器件', 'LITE/COHR：光学器件需求'),
    '300308.SZ': ('光模块', 'AAOI/LITE/COHR：数据中心互联需求'),
    '300502.SZ': ('光模块', 'AAOI/LITE/COHR：数据中心互联需求'),
    '002281.SZ': ('光模块及器件', 'AAOI/LITE/COHR：互联需求'),
    '603083.SH': ('光模块', 'AAOI：模块需求及竞争'),
    '688205.SH': ('光模块及传输', 'AAOI/GLW：互联及长距传输'),
    '301205.SZ': ('光模块', 'AAOI：模块需求及竞争'),
    '300757.SZ': ('设备，间接映射', '产业扩产资本开支，须核实订单'),
    '688025.SH': ('设备，间接映射', '光学加工检测，须核实产品及订单'),
}


def gaps(r):
    checks = [(r.close > r.ma20, '收盘未站上MA20'),
              (r.ma20 > r.ma60, 'MA20未上穿MA60'),
              (r.slope20 > 0, 'MA20未向上'),
              (r.slope60 > 0, 'MA60未向上')]
    result = [label for ok, label in checks if not ok]
    if r.vs_ma20_pct > 10:
        result.append('距MA20超过10%')
    if r.ret20 > .35:
        result.append('20日涨幅超过35%')
    return '；'.join(result) or '趋势条件齐备，仍检查形态触发'


def anchor(code, target):
    number, exchange = code.split('.')
    path = Path('C:/new_tdx/vipdoc') / exchange.lower() / 'lday' / f'{exchange.lower()}{number}.day'
    if not path.exists():
        return {'ts_code': code, 'anchor_status': '文件缺失'}
    data = path.read_bytes()
    if len(data) % 32:
        raise ValueError(f'Invalid TDX length: {path}')
    for offset in range(len(data)-32, -1, -32):
        row = struct.unpack_from('<IIIIIfII', data, offset)
        if row[0] == int(target):
            return {'ts_code': code, 'raw_date': target, 'raw_open': row[1]/100,
                    'raw_high': row[2]/100, 'raw_low': row[3]/100, 'raw_close': row[4]/100,
                    'raw_volume': row[6], 'anchor_status': '已读取', 'source': str(path)}
    return {'ts_code': code, 'anchor_status': '目标日缺失'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='20260908')
    args = ap.parse_args()
    stamp = args.date
    out = ROOT/'output'
    event = json.loads((ROOT/'research_inputs/us_optical_20260908.json').read_text(encoding='utf8'))
    if stamp != event['a_share_date'].replace('-', ''):
        raise ValueError('Event snapshot is date-specific; provide a new reviewed event for a new date')
    s = pd.read_csv(out/f'right_side_{stamp}_all.csv')
    selected = pd.read_csv(out/f'right_side_{stamp}_candidates.csv')
    summary = json.loads((out/f'right_side_{stamp}.json').read_text(encoding='utf8'))
    optics = s[s.ts_code.isin(MAPPING)].copy()
    optics['chain'] = optics.ts_code.map(lambda c: MAPPING[c][0])
    optics['mapping'] = optics.ts_code.map(lambda c: MAPPING[c][1])
    optics['gaps'] = optics.apply(gaps, axis=1)
    codes = set(optics.ts_code) | set(selected.ts_code)
    receipts = pd.DataFrame([anchor(c, stamp) for c in sorted(codes)])
    receipts = receipts.merge(s[['ts_code','open','high','low','close']], on='ts_code', validate='one_to_one')
    receipts['max_ohlc_diff'] = receipts.apply(lambda r: max(abs(r[k]-r['raw_'+k]) for k in ['open','high','low','close']) if r.anchor_status == '已读取' else float('inf'), axis=1)
    receipts.to_csv(out/f'right_side_{stamp}_tdx_anchors.csv', index=False, encoding='utf-8-sig')
    if not (receipts.max_ohlc_diff < .011).all():
        raise ValueError('Candidate TDX anchors failed; inspect receipts')
    d = pd.read_parquet(out/f'right_side_{stamp}_history.parquet')
    f = formula(d[d.ts_code.isin(codes)].copy())
    f = f[f.trade_date == pd.Timestamp(event['a_share_date'])]
    f['formula_stages'] = f.apply(lambda r: '/'.join(k for k in ['YJ','QD','QR','QQR'] if r[k]) if r.XG else '未命中', axis=1)
    optics = optics.merge(f[['ts_code','formula_stages']], on='ts_code', validate='one_to_one')
    optics.to_csv(out/f'right_side_{stamp}_optical.csv', index=False, encoding='utf-8-sig')
    def table(df, pairs):
        return '<div class="table">'+df[[x[0] for x in pairs]].rename(columns=dict(pairs)).to_html(index=False, border=0, float_format=lambda v: f'{v:.2f}', na_rep='未覆盖', escape=True)+'</div>'
    page = (out/f'a_share_analysis_{stamp}_right_side.html').read_text(encoding='utf8')
    intro = f'''<h2>9月9日简报：形态优先，不追海外涨幅</h2>
    <p><strong>当前没有一只重点光通信映射股通过本次严格形态筛选。</strong>这不等于行业转空，而是长期RPS高与当下买点合格是两回事。本次16只映射股，15只均线结构未齐，太辰光因20日累计涨幅过大进入过热等待。</p>
    <p>全市场优先复核：成都银行放量突破；金田股份缩量平台，等待突破。安德利属于回踩转强备选。亚泰集团虽入选但结构风险距离约12.6%，不因排序靠前就提高优先级；一博科技RPS50仅约33，短期偏弱。以上均为条件候选，公告、财报和账户风险尚未逐项复核，不是无条件买入清单。</p>
    <p>参考触发/禁追/失效价格见候选表。次日高开超过禁追位则放弃追入；突破后量能不持续则观察。没有分钟数据，不能声称已出现承接、资金流入或盘中买点。</p>
    <h2>美股9月8日盘中，不是收盘</h2>
    <p>行情时间为北京时间2026-09-08 23:47附近，当前报告已跨至9月9日；数据存在延迟，不能当作此刻实时价。LITE +12.19%、COHR +9.53%、AAOI +9.00%、GLW +9.24%。来源：行情工具盘中快照，逐股时间见留档；<a href="https://finance.sina.com.cn/wm/2026-09-08/doc-inircrnq6032160.shtml">21世纪经济报道的较早盘中报道</a>亦记录光通信上涨。</p>
    <p><strong>已核实事实：</strong><a href="{html.escape(event['primary_source'])}">Verizon 9月8日官方公告</a>：与康宁达成数十亿美元供应协议，2027至2032年供应超过8000万英里的高密度光纤及连接方案，面向宽带扩张与AI长距骨干网，并支持康宁扩大美国制造。</p>
    <p><strong>推断：</strong>首先增强高密度光纤与连接需求的可见度，再间接支持互联产业景气预期。不能将协议金额外推为A股订单，更不能推出所有CPO公司利润同步上调；美国本土制造、技术路线和竞争关系均可能削弱传导。未取得卖方原始报告的目标价或盈利上调，本次不作为已证实事实。</p>
    <h2>产业链映射与买点缺口</h2><p>下表是研究用产品环节映射，不是通达信概念成分，不代表已确认供货关系。光芯片、模块、光缆、设备不属于同一种产品；设备映射最间接。ZZ1234沿用用户原公式，公式命中不等于严格趋势通过。</p>'''
    intro += table(optics, [('name','股票'),('ts_code','代码'),('chain','产业环节'),('mapping','海外映射'),('shape','严格形态'),('formula_stages','ZZ1234'),('close','收盘元'),('RPS_50','RPS50'),('RPS_120','RPS120'),('vr','前20日量比'),('vs_ma20_pct','距MA20%'),('gaps','待改善条件')])
    intro += '<p>光通信观察顺序：光纤光缆先看长飞、亨通、中天是否形成共同走强；模块看中际、新易盛能否收复均线并改善成交量；器件看天孚、太辰光。顺序是信息核验顺序，不是收益排名。高开长上影、放量不涨、仅单股冲高时，撤销当日追涨假设。</p>'
    intro += f'<p>本次对所有候选及16只映射股合计{len(receipts)}只，逐只读取通达信9月8日原始OHLC，与最新归一前复权价格核对，全部误差小于0.011元。历史指标仍使用经本地运营一致性验证的等比前复权库，未把未复权日线直接用于跨日RPS。不是独立外部复权证明。</p>'
    page = page.replace('<main>', '<main>'+intro, 1)
    appendix = '<h2>重点光通信形态图</h2>'
    for code in ['601869.SH','300308.SZ','300502.SZ','300394.SZ','300570.SZ','688498.SH']:
        r = optics[optics.ts_code == code].iloc[0]
        appendix += f'<article><h3>{html.escape(r["name"])} {code}</h3><p>{html.escape(r.gaps)}。原公式：{r.formula_stages}。</p>'+chart(d[d.ts_code == code])+'</article>'
    appendix += '<h2>原公式审计与后续检验</h2><p>9月7日截图：57只中56只沪深可覆盖，55只命中原公式；47只触发乖离或爆量警戒。QQR已包含于QR，合并选股时不增加入选名单；DD窗口并未保证先出现高点再出现低点；QR不约束距MA20上限，也不要求放量。因此适合发现反弹候选，不宜直接替代买点。</p><p><a href="a_share_analysis_20260907_tdx_formula_audit.html">9月7日公式逐股审计</a>单独保留，不混作9月8日行情。新增交易日继续保存同口径快照，追踪+1/+5/+10/+20日收益及最大不利波动；本次未完成样本外回测，不能给出胜率或预期收益承诺。</p>'
    page = page.replace('</main>', appendix+'</main>')
    path = out/f'a_share_analysis_{stamp}_right_side_optical.html'
    path.write_text(page, encoding='utf8')
    brief = '# 2026-09-09观察简报\n\nA股截至9月8日收盘，美股为9月8日23:47北京时间附近的盘中快照。\n\n严格筛选没有合格的重点光通信买点；海外上涨只增加观察优先级。\n\n'+selected[selected.ts_code.isin(['601838.SH','601609.SH','605198.SH'])][['name','shape','trigger','no_chase','invalid']].to_string(index=False)+'\n\n须确认放量突破或回踩转强，高开超过禁追位不追。公告和账户风险未核对，不是无条件买入建议。模型仓位上限35%仅为未完成样本外验证的参考，不是目标仓位。\n'
    (out/f'right_side_{stamp}_optical_brief.md').write_text(brief, encoding='utf8')
    print(json.dumps({'report': str(path), 'anchors': len(receipts), 'counts': summary['counts'], 'optical': optics[['name','formula_stages','gaps']].to_dict('records')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
