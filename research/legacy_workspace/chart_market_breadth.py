"""Reproducible gated market breadth dashboard; no estimated net fund flow."""
from __future__ import annotations
import argparse, json, sys, html
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/a-share-rotation-trader/scripts'))
from run_integrated_decision import load_hithink_qfq, load_industry_map, load_industry_names, industry_label, DEFAULT_HQ, DEFAULT_INCON

def build(date):
    out=ROOT/'output'; target=pd.Timestamp(date)
    gate=json.loads((out/'qfq_verification.json').read_text(encoding='utf-8'))
    assert gate['price_adjustment']=='verified_qfq', 'G0未通过，停止价格指标'
    assert str(gate['full_market_checks']['latest_anchor']['max_date'])==date, 'G0日期不匹配'
    df,meta=load_hithink_qfq(ROOT/'data/hithink/market.duckdb', target, n_bars=360, min_bars=0)
    assert df.trade_date.max()==target
    df=df.sort_values(['ts_code','trade_date']).reset_index(drop=True)
    g=df.groupby('ts_code',sort=False)
    df['bars']=g.cumcount()+1
    df['prev']=g.close.shift(1)
    df['ret']=df.close/df.prev-1
    active=(df.vol>0)&(df.amount>0)
    for n in (5,10,20,60,120,250):
        df[f'ma{n}']=g.close.transform(lambda x:x.rolling(n,min_periods=n).mean())
    for n in (20,60,250):
        df[f'hi{n}']=g.close.transform(lambda x:x.shift(1).rolling(n,min_periods=n).max())
        df[f'lo{n}']=g.close.transform(lambda x:x.shift(1).rolling(n,min_periods=n).min())
    days=sorted(df.trade_date.unique())[-60:]
    breadth=[]
    for d in days:
        z=df.loc[(df.trade_date==d)&active]
        row={'date':pd.Timestamp(d).strftime('%Y-%m-%d'),'active':len(z),'amount_yi':float(z.amount.sum()/1e8),
             'up_pct':float((z.loc[z.prev.notna(),'ret']>0).mean()*100)}
        for n in (5,10,20,60,120,250):
            valid=z.loc[z[f'ma{n}'].notna()]
            row[f'ma{n}_denom']=len(valid)
            row[f'ma{n}_count']=int((valid.close>valid[f'ma{n}']).sum())
            row[f'ma{n}']=float((valid.close>valid[f'ma{n}']).mean()*100)
        for n in (20,60,250):
            v=z.loc[z[f'hi{n}'].notna()&z[f'lo{n}'].notna()]
            row[f'hl{n}_denom']=len(v)
            row[f'high{n}']=int((v.close>v[f'hi{n}']+1e-9).sum())
            row[f'low{n}']=int((v.close<v[f'lo{n}']-1e-9).sum())
        breadth.append(row)
    imap=load_industry_map(Path(DEFAULT_HQ)/'tdxhy.cfg')
    names=load_industry_names(Path(DEFAULT_INCON))
    imap['sector']=imap.industry.map(lambda x:industry_label(x,names))
    latest=df.loc[(df.trade_date==target)&active,['ts_code','amount','ret']]
    previous_date=pd.Timestamp(days[-2])
    prior=df.loc[(df.trade_date==previous_date)&active,['ts_code','amount']].rename(columns={'amount':'prev_amount'})
    pair=latest.merge(prior,on='ts_code',validate='one_to_one').merge(imap[['ts_code','sector']],on='ts_code',validate='one_to_one')
    unassigned=int(len(latest)-len(latest.merge(imap,on='ts_code')))
    sector=pair.groupby('sector').agg(amount=('amount','sum'),prev_amount=('prev_amount','sum'),members=('ts_code','size'),median_ret=('ret','median')).reset_index()
    sector['amount_yi']=sector.amount/1e8
    sector['prev_amount_yi']=sector.prev_amount/1e8
    sector['delta_yi']=(sector.amount-sector.prev_amount)/1e8
    sector['change_pct']=(sector.amount/sector.prev_amount-1)*100
    sector['share_pct']=sector.amount/sector.amount.sum()*100
    sector['prev_share_pct']=sector.prev_amount/sector.prev_amount.sum()*100
    sector['share_delta_pp']=sector.share_pct-sector.prev_share_pct
    sector['median_ret_pct']=sector.median_ret*100
    assert abs(sector.delta_yi.sum()-(pair.amount.sum()-pair.prev_amount.sum())/1e8)<1e-7
    assert abs(sector.share_delta_pp.sum())<1e-7
    for r in breadth:
        for n in (5,10,20,60,120,250):
            assert 0<=r[f'ma{n}_count']<=r[f'ma{n}_denom'] and 0<=r[f'ma{n}']<=100
        for n in (20,60,250):
            assert r[f'high{n}']+r[f'low{n}']<=r[f'hl{n}_denom']
    payload={'asof':date,'previous':previous_date.strftime('%Y-%m-%d'),'generated':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
             'breadth':breadth,'sectors':json.loads(sector.to_json(orient='records',force_ascii=False)),
             'quality':{**meta,'active_today':len(latest),'paired_mapped_stocks':len(pair),'unmapped_today':unassigned,'industry_file_updated':datetime.fromtimestamp((Path(DEFAULT_HQ)/'tdxhy.cfg').stat().st_mtime,ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')},
             'fund_flow_status':'not_available','scope':'沪深A股，不含北交所；含ST；每日仅保留有成交记录',
             'definitions':{'ma':'收盘价严格高于对应N日简单均线；每条均线按具备N日历史的活跃股票作分母',
                            'new_high_low':'收盘价严格突破此前N根日K（不含当日）的最高/最低收盘价；每个窗口至少N+1根',
                            'sector':'通达信行业名称映射，同名显示层合并；当前行业归属用于全部历史，非历史成分版本',
                            'turnover':'两日均交易且有行业映射的相同股票，汇总原始成交额；买卖总成交金额，无法识别净流入'}}
    tag=date.replace('-','')
    (out/f'market_breadth_{tag}.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    pd.DataFrame(breadth).to_csv(out/f'market_breadth_{tag}.csv',index=False,encoding='utf-8-sig')
    sector.to_csv(out/f'market_sector_turnover_{tag}.csv',index=False,encoding='utf-8-sig')
    render(payload,out/f'a_share_analysis_{tag}_market_charts.html')
    print(json.dumps({'latest':breadth[-1],'previous':breadth[-2],'top_increase':sector.nlargest(3,'delta_yi')[['sector','delta_yi','share_delta_pp']].to_dict('records'),'top_decrease':sector.nsmallest(3,'delta_yi')[['sector','delta_yi','share_delta_pp']].to_dict('records'),'quality':payload['quality']},ensure_ascii=False))

def render(data,path):
    template='''<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>10月13日 A股市场体检</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#22334b;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1250px;margin:auto;padding:24px}header{background:#142b42;color:white;padding:30px;border-radius:12px}h1{font-size:28px;margin:8px 0}h2{font-size:22px;margin:0 0 8px}h3{font-size:18px}section{margin-top:22px;padding:25px;background:white;border-radius:10px}.sub,small{color:#64758a}header .sub{color:#d4deea}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px}.stat{border-left:3px solid #8caac1;padding:6px 16px}.stat b{display:block;font-size:25px}.hint{padding:14px 18px;background:#edf3f8;border-radius:6px;margin:14px 0}.warning{background:#fff3df}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0}select,button{font:inherit;border:1px solid #cad5df;background:white;padding:7px 12px;border-radius:5px}button[aria-pressed=true]{background:#193b59;color:white}svg{display:block;width:100%;height:auto}svg text{font-family:system-ui,"Microsoft YaHei",sans-serif;font-size:13px;fill:#506479}.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:14px}.legend i{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:6px}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:10px;text-align:left;border-bottom:1px solid #e3e8ef}th{white-space:nowrap}.up{color:#bb3943}.down{color:#208270}details{margin-top:15px}a{color:#235e97}#daily-detail{min-height:29px}@media(max-width:650px){main{padding:10px}section,header{padding:16px}.stats{grid-template-columns:1fr}svg text{font-size:15px}}@media print{section{break-inside:avoid}.controls{display:none}}
    </style></head><body><main><header><div class="sub" id="date-label"></div><h1>市场是在反弹，还是开始普遍转强？</h1><p id="summary"></p><div class="stats" id="stats"></div></header>
    <section id="sector"><h2>① 哪些行业交易更活跃？</h2><p>看成交额增加或减少，再看它占市场交易的份额是否提高。</p><div class="hint warning"><b>资金净流入：未取得可靠的10月13日历史数据。</b>以下为成交活跃度，不能把红柱读成“净买入”、绿柱读成“净卖出”。</div><div class="controls"><label>查看 <select id="sector-mode"><option value="delta_yi">成交额较前日增减（亿元）</option><option value="share_delta_pp">成交份额变化（百分点）</option></select></label></div><div id="sector-chart"></div><p id="sector-insight"></p><details><summary>展开全部行业明细</summary><div class="scroll" id="sector-table"></div></details><p class="sub" id="pair-note"></p></section>
    <section><h2>② 有多少股票站上均线？</h2><p>站上MA5＝短线较强；站上MA20＝中期结构较好；站上MA60＝更长的趋势较好。比例越高，行情越普遍。</p><div id="ma-current"></div><div class="hint" id="ma-note"></div><div class="controls"><label>历史长度 <select id="window"><option value="20">近20个交易日</option><option value="60" selected>近60个交易日</option></select></label><span>点击曲线上的日期看当天明细</span></div><div id="ma-history"></div><div class="legend" id="ma-legend"></div><div id="daily-detail" class="hint"></div></section>
    <section><h2>③ 创新高、新低的股票谁更多？</h2><p>新高增加＝强势股票扩散；新低增加＝风险仍在扩散。两者都看，比只看指数更容易发现分化。</p><div class="controls"><label>比较窗口 <select id="hl-window"><option value="20">20日：短线突破</option><option value="60" selected>60日：中期突破</option><option value="250">250日：约一年突破</option></select></label></div><div class="stats" id="hl-stats"></div><div id="hl-chart"></div><div class="legend"><span><i style="background:#bb3943"></i>创新高家数（实线）</span><span><i style="background:#208270"></i>创新低家数（虚线）</span></div><p id="hl-note"></p><details><summary>新高、新低的精确定义</summary><p>只比较收盘价。当前收盘价严格高于此前N根日K的所有收盘价，计为新高；严格低于则计为新低。此前窗口不含当日；相等不计突破。与软件的“盘中最高价/最低价新高新低”统计可能不同。</p></details></section>
    <section><h2>怎么判断下一步？</h2><div class="hint"><b>继续改善：</b>MA5、MA20占比持续抬升，新高多于新低，且行业成交份额的增加伴随多数个股走强。</div><div class="hint"><b>只是轮动：</b>短线占比回升，但MA20/60占比低、创新高扩散有限。更适合等待个股确认。</div><div class="hint warning"><b>修复失败：</b>短线占比回落，新低继续增加；停止把反弹解释为普遍上升趋势。</div><p class="sub">这些是描述性观察条件，未提供经过回测的择时阈值，也不直接生成买单。</p></section>
    <section><h2>口径与数据检查</h2><div id="quality"></div><p>前复权价格：同花顺生产库v_daily_qfq；成交额保留原始元口径。G0通过16/16抽样及全库一致性检查，属于本地运营一致性，不是独立外部复权证明。新高与均线计算只使用10月13日及以前数据。</p><p>本页包含新股和ST；每项按其历史是否足够分别计算分母。停牌及无成交日不纳入当天统计，不用旧价填补。与上一页存在少量样本差异：本页不剔除绝对日涨跌幅超过25%的记录，上一页市场汇总剔除了这些记录。</p><p>行业按当前通达信归属，同名显示层合并；无历史行业版本，仅供描述当前行情。成交额增加可能来自买卖分歧，无法证明资金流向。净流入未覆盖，不计算替代净流入。</p><p><a href="market_breadth___TAG__.json">计算数据JSON</a> · <a href="market_breadth___TAG__.csv">均线和新高新低每日数据</a> · <a href="market_sector_turnover___TAG__.csv">行业成交明细</a></p></section></main>
    <script id="data" type="application/json">__DATA__</script><script>
    const D=JSON.parse(document.getElementById('data').textContent), last=D.breadth.at(-1), prev=D.breadth.at(-2);
    const $=id=>document.getElementById(id), fmt=(x,n=1)=>Number(x).toFixed(n), sign=x=>(x>=0?'+':'')+fmt(x), ns='http://www.w3.org/2000/svg';
    function e(tag,attrs={},txt){const a=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))a.setAttribute(k,v);if(txt!==undefined)a.textContent=txt;return a}
    function svg(w,h,title){const s=e('svg',{viewBox:`0 0 ${w} ${h}`,role:'img','aria-label':title});s.append(e('title',{},title));return s}
    function stat(v,label){return `<div class="stat"><b>${v}</b>${label}</div>`}
    const regime=last.ma20<50&&last.ma60<50?'弱势中的修复：均线占比有所回升，但多数股票仍在中长期均线下方，新低多于新高，尚未普遍转强。':last.ma20>=50&&last.ma60>=50?'超过半数股票站上中长期均线，结构较健康；仍需确认新高是否扩散。':'中短周期分化，属于轮动或结构修复，强弱股票要分开看。';
    $('date-label').textContent=`数据截至 ${D.asof}收盘 · 沪深A股，不含北交所 · 活跃股票${last.active}只`;
    $('summary').textContent=regime;
    $('stats').innerHTML=stat(fmt(last.ma5)+'%','站上MA5 · 短线强度')+stat(fmt(last.ma20)+'%','站上MA20 · 中期结构')+stat(`${last.high60} / ${last.low60}`,'60日新高 / 新低家数');
    const colors={5:'#235e97',10:'#955caf',20:'#bf6d20',60:'#208270',120:'#66788d',250:'#344457'};
    function currentMA(){let s=svg(1000,300,'10月13日站上不同均线的股票占比，单位%');const periods=[5,10,20,60,120,250];s.append(e('text',{x:950,y:22,'text-anchor':'end'},'股票占比（%）'));
    periods.forEach((n,i)=>{let y=40+i*39,w=last['ma'+n]*6.5;s.append(e('text',{x:5,y:y+17},'MA'+n));s.append(e('rect',{x:100,y,width:650,height:23,fill:'#edf1f5'}));s.append(e('rect',{x:100,y,width:w,height:23,fill:colors[n]}));s.append(e('text',{x:780,y:y+17},`${fmt(last['ma'+n])}%  (${sign(last['ma'+n]-prev['ma'+n])}百分点)`));});$('ma-current').replaceChildren(s)}
    function history(container,series,ymax,title){const arr=D.breadth.slice(-Number($('window').value)),w=1000,h=340,l=65,r=25,t=30,b=45,plotw=w-l-r,ploth=h-t-b;let s=svg(w,h,title);const X=i=>l+i*plotw/(arr.length-1),Y=v=>t+ploth-v/ymax*ploth;
    for(let j=0;j<=4;j++){let v=ymax*j/4;s.append(e('line',{x1:l,x2:w-r,y1:Y(v),y2:Y(v),stroke:'#e3e9ef'}));s.append(e('text',{x:l-8,y:Y(v)+4,'text-anchor':'end'},fmt(v,0)));}
    s.append(e('text',{x:5,y:16},title.includes('占比')?'占比（%）':'家数（只）'));s.append(e('text',{x:w-r,y:h-5,'text-anchor':'end'},'交易日期'));
    if(ymax===100)s.append(e('line',{x1:l,x2:w-r,y1:Y(50),y2:Y(50),stroke:'#aebccc','stroke-dasharray':'5 5'}));
    for(const z of series){s.append(e('polyline',{points:arr.map((d,i)=>`${X(i)},${Y(d[z.key])}`).join(' '),fill:'none',stroke:z.color,'stroke-width':2.5,'stroke-dasharray':z.dash||''}));}
    const idx=[0,Math.floor((arr.length-1)/3),Math.floor(2*(arr.length-1)/3),arr.length-1];idx.forEach(i=>s.append(e('text',{x:X(i),y:h-23,'text-anchor':i===0?'start':i===arr.length-1?'end':'middle'},arr[i].date.slice(5))));
    arr.forEach((d,i)=>{const mark=e('rect',{x:X(i)-plotw/(arr.length-1)/2,y:t,width:plotw/(arr.length-1),height:ploth,fill:'transparent',tabindex:0,'aria-label':`${d.date}，${series.map(z=>z.label+' '+fmt(d[z.key])).join('；')}`});mark.append(e('title',{},`${d.date}：${series.map(z=>z.label+' '+fmt(d[z.key])).join('；')}`));const show=()=>{$('daily-detail').textContent=`${d.date} · MA5 ${fmt(d.ma5)}% / MA20 ${fmt(d.ma20)}% / MA60 ${fmt(d.ma60)}% · 60日新高${d.high60}只，新低${d.low60}只`};mark.addEventListener('click',show);mark.addEventListener('focus',show);s.append(mark)});$(container).replaceChildren(s);}
    function maHistory(){const p=[5,10,20,60];history('ma-history',p.map(n=>({key:'ma'+n,label:'MA'+n,color:colors[n]})),100,'近60个交易日站上均线占比变化');$('ma-legend').innerHTML=p.map(n=>`<span><i style="background:${colors[n]}"></i>MA${n}</span>`).join('')}
    function highs(){const n=Number($('hl-window').value),arr=D.breadth.slice(-Number($('window').value)),hi=last['high'+n],lo=last['low'+n];$('hl-stats').innerHTML=stat(hi+'只',n+'日收盘创新高')+stat(lo+'只',n+'日收盘创新低')+stat((hi-lo)+'只','新高减新低');let max=Math.max(...arr.flatMap(x=>[x['high'+n],x['low'+n]]),1);max=Math.ceil(max/50)*50;history('hl-chart',[{key:'high'+n,label:'新高',color:'#bb3943'},{key:'low'+n,label:'新低',color:'#208270',dash:'6 4'}],max,n+'日收盘新高与新低家数');$('hl-note').textContent=`${D.asof}，可计算${n}日突破的股票为${last['hl'+n+'_denom']}只。新高${hi>lo?'多于':hi<lo?'少于':'等于'}新低，${hi>lo?'突破端占优，但需看持续性。':hi<lo?'下跌端仍较多，反弹尚未覆盖整个市场。':'两端均衡，暂未出现明确扩散优势。'}`;}
    function sectors(){const key=$('sector-mode').value,all=[...D.sectors].sort((a,b)=>b[key]-a[key]),pos=all.filter(x=>x[key]>0).slice(0,8),neg=all.filter(x=>x[key]<0).slice(-8).reverse(),arr=[...pos,...neg],unit=key==='delta_yi'?'亿元':'百分点',W=1000,H=70+arr.length*37,l=150,axis=560,max=Math.max(...arr.map(x=>Math.abs(x[key])),1),scale=310/max,s=svg(W,H,`行业${key==='delta_yi'?'成交额增减':'成交份额变化'}，单位${unit}`);
    s.append(e('text',{x:150,y:22},'← 减少'));s.append(e('text',{x:780,y:22},'增加 →'));s.append(e('line',{x1:axis,x2:axis,y1:30,y2:H-25,stroke:'#8fa1b4'}));s.append(e('text',{x:axis,y:H-5,'text-anchor':'middle'},`0（${unit}）`));arr.forEach((d,i)=>{let y=35+i*37,v=d[key],bw=Math.abs(v)*scale,x=v>=0?axis:axis-bw;s.append(e('text',{x:5,y:y+18},d.sector));let b=e('rect',{x,y,width:bw,height:23,fill:v>=0?'#bb3943':'#208270'});b.append(e('title',{},`${d.sector}：${sign(v)}${unit}；今日成交额${fmt(d.amount_yi)}亿元；行业收益中位数${sign(d.median_ret_pct)}%`));s.append(b);s.append(e('text',{x:v>=0?axis+bw+8:axis-bw-8,y:y+18,'text-anchor':v>=0?'start':'end'},sign(v)));});$('sector-chart').replaceChildren(s);$('sector-insight').textContent=`增加最多：${pos[0]?.sector||'无'}；减少最多：${neg[0]?.sector||'无'}。这是交易活跃度方向，结合行业收益中位数判断放量是上涨还是下跌。`;
    $('sector-table').innerHTML='<table><thead><tr><th>行业</th><th>股票数</th><th>今日成交亿元</th><th>增减亿元</th><th>份额变化百分点</th><th>日收益中位数</th></tr></thead><tbody>'+all.map(x=>`<tr><td>${x.sector}</td><td>${x.members}</td><td>${fmt(x.amount_yi)}</td><td class="${x.delta_yi>=0?'up':'down'}">${sign(x.delta_yi)}</td><td>${sign(x.share_delta_pp)}</td><td>${sign(x.median_ret_pct)}%</td></tr>`).join('')+'</tbody></table>';
    }
    $('ma-note').textContent=`截至${D.asof}，站上MA5 ${fmt(last.ma5)}%、MA20 ${fmt(last.ma20)}%、MA60 ${fmt(last.ma60)}%。较${D.previous}分别变化${sign(last.ma5-prev.ma5)}、${sign(last.ma20-prev.ma20)}、${sign(last.ma60-prev.ma60)}个百分点。20日新低由${prev.low20}降到${last.low20}只，抛压扩散减缓，但仍多于新高${last.high20}只。50%虚线只表示“一半股票”，不是回测买入阈值。`;
    $('pair-note').textContent=`比较${D.previous}与${D.asof}；采用两日都有成交、有行业映射的${D.quality.paired_mapped_stocks}只相同股票。当天未映射${D.quality.unmapped_today}只。份额分母为该配对样本的全部成交额。`;
    $('quality').textContent=`生成时间${D.generated}；历史图${D.breadth[0].date}—${D.asof}，60个交易日。重复行${D.quality.duplicate_rows}，空价格${D.quality.null_prices}，异常价格${D.quality.invalid_prices}。行业归属文件更新：${D.quality.industry_file_updated}。`;
    currentMA();maHistory();highs();sectors();$('daily-detail').textContent='点击历史曲线上的日期可查看该日占比和突破家数。';$('window').addEventListener('change',()=>{maHistory();highs()});$('hl-window').addEventListener('change',highs);$('sector-mode').addEventListener('change',sectors);
    </script></body></html>'''
    content=template.replace('__TAG__',data['asof'].replace('-','')).replace('__DATA__',json.dumps(data,ensure_ascii=False,allow_nan=False).replace('</','<\\/'))
    assert '__DATA__' not in content and '__TAG__' not in content
    path.write_text(content,encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',default='2026-10-13');build(p.parse_args().date)
