"""Auditable shape-first research using the project's verified adjusted data."""
from __future__ import annotations

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
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = PROJECT_ROOT
sys.path.insert(0, str(ROOT / '.agents/skills/a-share-rotation-trader/scripts'))
import run_integrated_decision as core


def metrics(daily):
    d = core.add_indicators(daily)
    g = d.groupby('ts_code', sort=False)
    for n in (10, 120):
        d[f'ma{n}'] = g.close.transform(lambda s: s.rolling(n).mean())
    for n in (20, 60):
        d[f'slope{n}'] = d[f'ma{n}'] / d.groupby('ts_code')[f'ma{n}'].shift(5) - 1
    for n in (5, 10, 20):
        d[f'ph{n}'] = g.high.transform(lambda s: s.shift(1).rolling(n).max())
        d[f'pl{n}'] = g.low.transform(lambda s: s.shift(1).rolling(n).min())
    d['vref20'] = g.vol.transform(lambda s: s.shift(1).rolling(20).mean())
    d['vref5'] = g.vol.transform(lambda s: s.shift(1).rolling(5).mean())
    d['vr'] = d.vol / d.vref20.replace(0, np.nan)
    d['dry'] = d.vref5 / d.vref20.replace(0, np.nan)
    d['width10'] = d.ph10 / d.pl10 - 1
    d['width20'] = d.ph20 / d.pl20 - 1
    d['clv'] = (d.close - d.low) / (d.high - d.low).replace(0, np.nan)
    d['atrpct'] = d.atr20 / d.close * 100
    d['upvol'] = d.vol.where(d.ret1 > 0, 0)
    d['downvol'] = d.vol.where(d.ret1 < 0, 0)
    d['ud20'] = d.groupby('ts_code').upvol.transform(lambda s: s.rolling(20).sum()) / d.groupby('ts_code').downvol.transform(lambda s: s.rolling(20).sum()).replace(0, np.nan)
    return core.calculate_rps(d, periods=[50, 120, 250], verbose=False)


def classify(r):
    if r.bars_count < 120 or r['name'] == r.ts_code or 'ST' in str(r['name']).upper() or '退' in str(r['name']):
        return '排除', '历史不足120日或风险警示名称'
    if r.adv20 < 1e8 or r.amount <= 0:
        return '排除', '前20日成交额中位数不足1亿元或无成交'
    if not (r.close > r.ma20 > r.ma60 and r.slope20 > 0 and r.slope60 > 0):
        return '趋势未齐', '尚未同时满足收盘>MA20>MA60且两线向上'
    if r.vs_ma20_pct > 10 or r.ret20 > 0.35:
        return '过热等待', '距MA20超过10%或20日涨幅超过35%'
    if r.ret1 < 0 and r.vr > 1.5 and r.clv < 0.35:
        return '放量转弱', '放量下跌且收盘位于当日振幅下部'
    if r.close > r.ph20 and 1.3 <= r.vr <= 3 and r.clv >= 0.65 and r.width20 <= 0.25 and r.ud20 >= 1:
        return '放量突破', '突破前20日高点，量比1.3至3，收盘强，前平台振幅≤25%'
    if r.low <= r.ma20 * 1.025 and r.close >= r.ma20 and r.close > r.open and r.close > r.ph5 and r.dry <= 0.85 and r.vr >= 1 and r.clv >= 0.6:
        return '回踩转强', '缩量整理后触及MA20附近，阳线越过前5日高点并恢复成交量'
    if r.width10 <= 0.12 and r.dry <= 0.9 and 0.95 <= r.close / r.ph20 <= 1.015 and r.vr <= 1.3:
        return '平台待突破', '近10日振幅≤12%，整理缩量，靠近前20日高点；仍需放量确认'
    return '趋势观察', '趋势完整，尚未出现本次定义的突破或回踩触发'


def chart(d):
    d = d.tail(80).reset_index(drop=True)
    lo, hi = d.low.min(), d.high.max()
    width, top, floor = 850, 20, 230
    step = 770 / len(d)
    y = lambda v: floor - (v-lo) / max(hi-lo, 0.01) * (floor-top)
    bits = ['<svg viewBox="0 0 850 345" role="img" aria-label="80个交易日前复权K线、MA20和MA60、原始成交量">']
    for i, r in d.iterrows():
        x = 55 + i * step
        color = '#c53646' if r.close >= r.open else '#138264'
        bits.append(f'<path d="M{x:.1f},{y(r.high):.1f}V{y(r.low):.1f}" stroke="{color}"/>')
        bits.append(f'<rect x="{x-step*.3:.1f}" y="{min(y(r.open),y(r.close)):.1f}" width="{step*.6:.1f}" height="{max(abs(y(r.open)-y(r.close)),1):.1f}" fill="{color}"/>')
        h = r.vol / max(d.vol.max(),1) * 65
        bits.append(f'<rect x="{x-step*.3:.1f}" y="{320-h:.1f}" width="{step*.6:.1f}" height="{h:.1f}" fill="{color}"/>')
    for col, color in [('ma20','#b57900'),('ma60','#396aac')]:
        pts = ' '.join(f'{55+i*step:.1f},{y(v):.1f}' for i,v in enumerate(d[col]) if pd.notna(v))
        bits.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"/>')
    bits.append(f'<text x="0" y="25">{hi:.2f}</text><text x="0" y="230">{lo:.2f}</text><text x="55" y="340">{d.trade_date.iloc[0]:%m-%d}</text><text x="740" y="340">{d.trade_date.iloc[-1]:%m-%d}</text></svg>')
    return ''.join(bits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    args = ap.parse_args()
    target = pd.Timestamp(args.date)
    out = ROOT / 'output'
    stamp = target.strftime('%Y%m%d')
    gate = json.loads((out/'qfq_verification.json').read_text(encoding='utf-8-sig'))
    if gate.get('price_adjustment') != 'verified_qfq' or gate.get('full_market_checks',{}).get('latest_anchor',{}).get('max_date') != target.strftime('%Y-%m-%d'):
        raise RuntimeError('目标交易日复权校验未通过，请运行verify_qfq.py')
    eco = json.loads((out/'limit_ecology_latest.json').read_text(encoding='utf-8-sig'))
    eco['cap_note'] = eco.get('cap_note') or '未触发额外硬覆盖；模型尚未完成样本外验证'
    if eco['as_of_trade_date'] != target.strftime('%Y-%m-%d'):
        raise RuntimeError('情绪快照日期不一致，请刷新build_limit_ecology.py')
    daily, meta = core.load_hithink_qfq(core.DEFAULT_HITHINK_DB, target, 360, min_bars=120)
    master = core.load_name_master(core.DEFAULT_HQ)
    daily['name'] = daily.ts_code.map(master).fillna(daily['name']).fillna(daily.ts_code)
    d = metrics(daily)
    d.to_parquet(out/f'right_side_{stamp}_history.parquet', index=False)
    s = d[d.trade_date == target].copy()
    if s.empty:
        raise RuntimeError('目标日没有行情')
    imap = core.load_industry_map(core.DEFAULT_HQ/'tdxhy.cfg')
    names = core.load_industry_names(core.DEFAULT_INCON)
    industries = core.rank_industries(s, imap, names)
    industries['industry_rank'] = np.arange(1,len(industries)+1)
    s = s.merge(imap, on='ts_code', how='left').merge(industries[['industry','industry_rank','breadth_ma20_pct','resonance']],on='industry',how='left')
    s['industry_name'] = s.industry.map(lambda c: core.industry_label(c,names) if isinstance(c,str) else '未归类')
    states = s.apply(classify, axis=1)
    s['shape'] = [x[0] for x in states]
    s['reason'] = [x[1] for x in states]
    s['trend_tier'] = np.where((s.ma60 > s.ma120) & (s.RPS_120 >= 80),'成熟趋势','早期趋势')
    s['tier_order'] = np.where(s.trend_tier == '成熟趋势',0,1)
    order = {'放量突破':0,'回踩转强':1,'平台待突破':2,'趋势观察':3,'过热等待':4,'放量转弱':5,'趋势未齐':6,'排除':7}
    s['priority'] = s['shape'].map(order)
    s = s.sort_values(['tier_order','priority','industry_rank','RPS_120'], ascending=[True,True,True,False])
    selected = s[s['shape'].isin(['放量突破','回踩转强','平台待突破'])].copy()
    selected['trigger'] = np.maximum(selected.high, selected.ph20)
    selected.loc[selected['shape']=='回踩转强','trigger'] = selected.high
    selected['no_chase'] = selected.trigger + selected.atr20 * 0.35
    selected['invalid'] = np.where(selected['shape']=='回踩转强', selected[['low','pl5']].min(axis=1), selected[['pl10','ma20']].min(axis=1))
    selected['planned_stop_pct'] = (selected.trigger-selected.invalid)/selected.trigger*100
    selected['limit_floor_pct'] = selected.ts_code.map(lambda c: core.limit_pct_for(c)*60)
    selected['effective_stop_pct'] = selected[['planned_stop_pct','atrpct','limit_floor_pct']].max(axis=1)
    cap = min(float(eco['cycle_position_cap_pct']),35)
    selected['example_position_pct'] = np.minimum(0.3/selected.effective_stop_pct*100,cap)
    selected['hypothesis_2R'] = selected.trigger + 2*(selected.trigger-selected.invalid)
    s.to_csv(out/f'right_side_{stamp}_all.csv',index=False,encoding='utf-8-sig')
    selected.to_csv(out/f'right_side_{stamp}_candidates.csv',index=False,encoding='utf-8-sig')
    industries.to_csv(out/f'right_side_{stamp}_industries.csv',index=False,encoding='utf-8-sig')
    summary = {'date':str(target.date()),'generated_at':datetime.now().isoformat(),'meta':meta,'counts':s['shape'].value_counts().to_dict(),'coverage':len(s),'above_ma20_pct':float((s.close>s.ma20).mean()*100),'above_ma60_pct':float((s.close>s.ma60).mean()*100),'up':int((s.ret1>0).sum()),'down':int((s.ret1<0).sum()),'ecology':eco,'account_cap_reference_pct':cap,'selection_mode':'shape_first_all_industries_v1','top':json.loads(selected.head(18).to_json(orient='records',date_format='iso'))}
    (out/f'right_side_{stamp}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    cols = ['ts_code','name','industry_name','shape','trend_tier','close','RPS_50','RPS_120','vr','dry','vs_ma20_pct','trigger','no_chase','invalid','example_position_pct']
    labels = ['代码','名称','行业','形态','趋势阶段','收盘','RPS50','RPS120','量比','整理量比','距MA20%','触发参考','追高禁区起点','结构失效','示例仓位%']
    def table(df, columns, labels):
        return df[columns].rename(columns=dict(zip(columns,labels))).to_html(index=False,escape=True,float_format=lambda x:f'{x:.2f}',na_rep='未覆盖',border=0)
    fragments = [f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{stamp} 右侧趋势选股</title><style>body{{font:16px system-ui,"Microsoft YaHei";margin:0;background:#f4f6f7;color:#202a30;line-height:1.65;letter-spacing:0}}main{{max-width:1350px;margin:auto;padding:24px}}header{{background:#193d36;color:white;padding:24px}}h1{{font-size:28px}}h2{{font-size:22px;margin-top:32px}}h3{{font-size:18px}}.table{{overflow:auto}}table{{border-collapse:collapse;font-size:13px;width:100%;background:white}}th,td{{padding:9px;border-bottom:1px solid #ddd;white-space:nowrap;text-align:right}}th{{background:#e4ecea}}article{{border-top:2px solid #bbc9c4;padding:16px 0}}svg{{width:100%;height:auto;background:white}}p{{max-width:1100px}}a{{color:#166557}}@media(max-width:600px){{main{{padding:12px}}header{{padding:16px}}h1{{font-size:23px}}}}@media print{{.table{{overflow:visible}}table{{font-size:9px}}article{{break-inside:avoid}}}}</style><header><h1>{stamp} 右侧趋势、形态与量价选股</h1><p>数据截至 {target:%Y-%m-%d} 收盘；面向下一交易日。形态优先、各行业开放筛选，情绪与龙头反馈仅用于仓位参考。</p><p>当前动作：等待候选价格与量能触发。保守仓位参考上限 {cap:.0f}%；账户持仓与风险预算未提供，以下仓位仅以单笔权益风险0.3%示例。</p></header><main>']
    fragments += [f'<p>宏观市场状态推断：高位轮动；盘面过程推断：修复。量化情绪分 {eco["ST_cycle"]:.1f}，模型状态“{eco["cycle_state"]}”，原因：{html.escape(eco.get("cap_note", ""))}。这个未完成样本外验证的模型只作参考，不以连板身份决定个股入选，也不据此强制卖出结构完整的趋势仓。</p>',f'<p>沪深样本 {len(s)} 只，上涨 {summary["up"]} / 下跌 {summary["down"]}；站上MA20 {summary["above_ma20_pct"]:.1f}%，MA60 {summary["above_ma60_pct"]:.1f}%。本地涨停 {eco["raw"]["limit_up_n"]:.0f}，跌停 {eco["raw"]["limit_down_n"]:.0f}，炸板率 {eco["raw"]["FR"]*100:.1f}%。</p>', '<p>事实核对：<a href="https://www.cnfin.com/yw-lb/detail/20260907/4466142_1.html">新华财经9月7日收评</a>披露沪指+0.07%、深成指+1.91%、创业板指+3.41%。本地个股样本成交额与交易所市场总额范围不同，不混算。分钟行情、当前持仓、即时委托信息未覆盖；日线不能证明盘中承接。</p>', '<h2>筛选分层</h2><p>'+html.escape(str(summary['counts']))+'</p><p>阈值是可复算的研究规则，尚未完成本组合样本外检验。成熟趋势与早期趋势分开标注；平台待突破仍需确认，不能按收盘价直接买入。所有价格为前复权，目标日最新因子已归一；复权验证范围为本地运营一致性验证，非独立外部证明。</p>','<h2>候选简报</h2><div class="table">',table(selected.head(18),cols,labels),'</div><p>排序：形态类别 → 行业共振排名 → RPS120；不把情绪、形态、行业加成一个总分。完整名单见同日期candidates.csv。</p>','<h2>行业支持度</h2><div class="table">',table(industries.head(15),['name','members','ret5_pct','ret20_pct','breadth_ma20_pct','median_rps120','amount_vs_adv20','single_stock_driven'],['行业','样本数','5日中位%','20日中位%','MA20宽度%','RPS120中位','量额比','单股驱动']),'</div>']
    for _,r in selected.head(12).iterrows():
        if r.RPS_50 < 60:
            fragments.append('<p><strong>短周期提示：</strong>'+html.escape(r['name'])+' 的RPS50低于60，长期强度不等于近期领先；按修复型候选对待，等待突破而非直接追入。</p>')
        fragments += [f'<article><h2>{html.escape(r["name"])} {r.ts_code} · {r["shape"]}</h2><p>{html.escape(r.reason)}。{html.escape(r.industry_name)}，{r.trend_tier}；上涨日/下跌日20日累计成交量比 {r.ud20:.2f}。</p>',chart(d[d.ts_code==r.ts_code]),f'<p>图：最近80个交易日前复权K线；金色MA20、蓝色MA60；下方为原始成交量，柱高相对本股窗口最大值。突破参考 {r.trigger:.2f} 元，禁追参考 {r.no_chase:.2f} 元；结构失效 {r.invalid:.2f} 元。</p>',f'<p>交易假设：价格越过触发位后保持，完成交易日成交量至少达到前20日均量1.3倍，避免用早盘累计量直接与全天均量比较；若高开越过禁追价，取消当日追入。名义止损 {r.planned_stop_pct:.1f}%，ATR {r.atrpct:.1f}%，跌停缓冲 {r.limit_floor_pct:.1f}%，有效风险距离 {r.effective_stop_pct:.1f}%；0.3%权益风险下示例仓位 {r.example_position_pct:.1f}%。2R测算价 {r.hypothesis_2R:.2f} 元仅是名义风险的两倍，不是盈利预测；未来空间未被保证。</p></article>']
    familiar=s[s.ts_code.isin(['300308.SZ','300502.SZ','002384.SZ','002281.SZ','002156.SZ','600584.SH','601869.SH','688072.SH','688536.SH'])]
    fragments += ['<h2>熟悉标的复核</h2><div class="table">',table(familiar,['ts_code','name','shape','close','RPS_120','vs_ma20_pct','vr','reason'],['代码','名称','状态','收盘','RPS120','距MA20%','量比','原因']),'</div><h2>执行与迭代</h2><p>空仓：仅在触发后试一只，总额遵守参考额度；已有仓：按个股失效位和MA20结构管理，不因当天情绪标签机械清仓；重仓：先核对相关性与可卖数量，停止盲目叠加同一行业。新买当日T+1不可卖；次日若跌破失效位，在可成交时退出，跌停时可能无法成交。收盘跌破MA20先减，连续两日未收复再退出；盈利超过1ATR且止损可上移至成本后才研究加仓。</p><p>主要风险：假突破、放量滞涨、行业共振回落、跳空导致实际损失超过计划。不操作可能错过突破后的延续段，但不存在可提前保证的涨幅。日线筛选尚未逐家复核公告与财报，交易前仍需核对候选当日重大披露。</p><p>复盘保存+1/+5/+10/+20交易日收益、最大有利/不利波动与失效原因。今日日后数据不得反写当日筛选条件。复现：python run_right_side_research.py --date '+str(target.date())+'</p><p>来源：本地同花顺v_daily_qfq、通达信原始日线情绪重建、tdxhy.cfg行业映射。行业映射是当前版本，历史复盘不能假设过去成员相同。覆盖沪深A股，不含北交所。生成时间 '+datetime.now().isoformat(timespec='seconds')+'</p></main></html>']
    path=out/f'a_share_analysis_{stamp}_right_side.html'
    if stamp != '20260907':
        fragments = [part for part in fragments if '新华财经9月7日收评' not in part]
    fragments = [part.replace('宏观市场状态推断：高位轮动；盘面过程推断：修复。', '宏观市场状态：本次未独立判定；盘面过程须结合次日反馈确认。') for part in fragments]
    fragments = [part.replace('排序：形态类别 → 行业共振排名 → RPS120', '排序：成熟/早期趋势分组 → 形态类别 → 行业共振排名 → RPS120') for part in fragments]
    path.write_text(''.join(fragments),encoding='utf-8')
    print(json.dumps({'report':str(path),'counts':summary['counts'],'top':summary['top'][:8]},ensure_ascii=False,default=str))


if __name__ == '__main__':
    main()
