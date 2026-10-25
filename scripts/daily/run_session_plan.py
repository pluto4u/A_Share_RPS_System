"""Freeze a dated local screen with event context and conditional execution rules."""
from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import html
import json
from datetime import datetime

import pandas as pd
from run_right_side_research import ROOT
from run_optical_overlay import anchor, gaps


def main():
    out = ROOT/'output'
    stamp = '20260909'
    s = pd.read_csv(out/f'right_side_{stamp}_all.csv')
    candidates = pd.read_csv(out/f'right_side_{stamp}_candidates.csv')
    summary = json.loads((out/f'right_side_{stamp}.json').read_text(encoding='utf8'))
    gate = json.loads((out/'qfq_verification.json').read_text(encoding='utf-8-sig'))
    assert gate['price_adjustment']=='verified_qfq'
    assert gate['full_market_checks']['latest_anchor']['max_date']=='2026-09-09'
    focus_codes = ['603903.SH','603979.SH','603338.SH','000333.SZ','601866.SH','601609.SH']
    watch_codes = ['601838.SH','605198.SH','688183.SH','002916.SZ','002463.SZ','600183.SH','688813.SH','601869.SH','300308.SZ','300502.SZ','300394.SZ','300570.SZ']
    focus = candidates.set_index('ts_code').loc[focus_codes].reset_index()
    watch = s.set_index('ts_code').loc[watch_codes].reset_index()
    watch['daily_change_pct'] = watch.ret1*100
    watch['gaps'] = watch.apply(gaps,axis=1)
    codes = set(candidates.ts_code) | set(watch_codes)
    receipts = pd.DataFrame([anchor(c,stamp) for c in sorted(codes)])
    receipts = receipts.merge(s[['ts_code','open','high','low','close']], on='ts_code',validate='one_to_one')
    if not (receipts.anchor_status=='已读取').all():
        raise ValueError('Missing TDX target date')
    receipts['max_ohlc_diff'] = receipts.apply(lambda r:max(abs(r[k]-r['raw_'+k]) for k in ['open','high','low','close']),axis=1)
    if not (receipts.max_ohlc_diff<.011).all():
        raise ValueError('TDX anchor disagreement')
    receipts.to_csv(out/f'session_plan_{stamp}_anchors.csv',index=False,encoding='utf-8-sig')
    focus.to_csv(out/f'session_plan_{stamp}_focus.csv',index=False,encoding='utf-8-sig')
    watch.to_csv(out/f'session_plan_{stamp}_watch.csv',index=False,encoding='utf-8-sig')
    def table(df, pairs):
        return '<div class="table">'+df[[c for c,_ in pairs]].rename(columns=dict(pairs)).to_html(index=False,border=0,float_format=lambda n:f'{n:.2f}',na_rep='历史不足',escape=True)+'</div>'
    intro = f'''<h2>时间校正与当前结论</h2><p>本报告使用9月9日A股收盘，面向下一交易日（按通常交易安排为9月10日）。9月9日23:55开始研究时A股已经收盘，美股9月9日仍在盘中；不把今晚尚未发生的收盘称为最终走势，不把收盘后获知的油价新闻当作早盘已知信息。</p>
    <p><strong>当前行动：研究候选，尚不可直接执行。</strong>最新公告、实际账户风险预算与盘中确认未齐；下面价位是触发研究的条件，不是委托指令。形态优先，覆盖各行业，不按连板龙头身份选股。</p>
    <h2>海外与国内的实际变化</h2><p>已完成的美股9月8日收盘：标普约-0.58%、纳指-0.32%，光通信Lumentum涨超11%、康宁及Coherent涨超7%。<a href="https://www.yicai.com/news/103356908.html">第一财经收盘快讯</a>。9月9日北京时间23:41至23:42附近延迟快照：LITE约+3.66%、COHR约+2.25%、GLW约+1.68%，均是盘中，不是当晚最终收盘。</p>
    <p>油价已不同于今早97.92美元的前一日结算口径：9月9日报道显示Brent期货突破100美元，AP记录的早盘快照约100.72美元，WTI约95.25美元。这里不将早盘快照冒充当前实时油价。<a href="https://apnews.com/article/7538e6386a819bcdc2547d530ec3472e">AP油价报道</a>。<a href="https://live.euronext.com/en/financial-news/brent-crude-rises-above-100-barrel-middle-east-conflict-intensifies">Reuters报道</a>将供应担忧与油轮、能源设施袭击联系起来，具体战果仍需区分交战方声明。</p>
    <p><strong>推断：</strong>能源供应风险增加通胀与风险偏好压力，不能据此认定所有石油、航运、军工股必涨；运输成本、保险、制裁和需求损失会产生反向影响。光通信催化存在，但本地价格仍有决定权。</p>
    <p>9月9日市场实际为指数分化、个股偏弱，外部收评记录逾3600股下跌、煤炭与航运较强。<a href="https://www.cls.cn/detail/2478296">财联社收评</a>。本地{summary['coverage']}只有效样本上涨{summary['up']}只、下跌{summary['down']}只，统计范围不同不强求相等。涨停52、跌停9、炸板30；样本成交额18330.41亿元，较昨日约缩5.4%。不是全面普涨，也不能证明所有下跌由战争造成。</p>
    <h2>六只重点研究候选</h2><p>保留原规则全部62只候选，下表是其中的人工复核清单，不是综合评分排名：中持用于观察放量突破，金诚信与浙江鼎力观察强势平台，美的观察较窄结构风险，中远海发单列早期趋势，金田用于跟踪原计划。没有据此证明它们是行业龙头。</p>'''
    intro += table(focus,[('name','股票'),('shape','形态'),('trend_tier','阶段'),('close','收盘元'),('RPS_50','RPS50'),('RPS_120','RPS120'),('vr','量比'),('trigger','触发参考'),('no_chase','禁追边界'),('invalid','结构失效'),('effective_stop_pct','有效风险%'),('example_position_pct','示例仓位%')])
    intro += '''<p>中持名义结构风险约12.3%，不是低风险买点；中远海发属于早期趋势，不能因当日航运强就当成成熟趋势。金田9月9日盘中高点进入新压力区，触发参考由昨日13.92上移为14.02；不沿用旧价。美的风险距离虽较窄，仍受跌停缓冲约束，不能用窄止损推导重仓。</p>
    <h2>买入与卖出策略</h2><p><strong>买入前：</strong>核对最新重大公告、停复牌及风险警示、账户现金和已有相关仓位。触发价上方且不超过禁追边界仅是价格条件；平台必须有效突破，完成日量至少达到此前20日均量1.3倍，且收盘位置偏强。若依靠盘中确认，需要真实的同时间量能和回踩保持证据，本次没有分钟数据，不能声称已确认。高开越过禁追边界、突破回落或市场风险明显恶化，取消追入；不把成交量未确认强行解释成锁筹。</p>
    <p><strong>新仓次日：</strong>在T+1允许卖出且可成交时，跌破事先确定的结构失效位按计划退出；跳空越过止损或跌停可能造成超额损失。收盘跌破MA20可减半，连续两日未收复或收盘跌破MA60则退出该笔趋势交易。不得把本次筛选落榜作为独立清仓理由，先区分结构失效和只是缺少新买点。</p>
    <p><strong>盈利管理：</strong>创新高后可按“此前失效位与最高价减2倍ATR20取较高值”上移保护位，只上移不下移；浮盈超过1ATR且保护位已到成本上方才研究加仓，整笔风险不得超过原预算，不补亏损仓。报告2R价只是算术测算，不是目标价或已证实的上涨空间。</p>
    <p><strong>仓位示例：</strong>表内以单笔账户权益风险0.3%除以有效风险距离；有效距离取名义止损、ATR占比和涨跌幅缓冲的最大值。示例不考虑你的实际持仓，不能直接照抄。模型35%为未完成样本外验证的参考上限，不是建议满额配置；当前账户可执行仓位因资料未齐不作数值承诺。</p>
    <p>空仓：允许一笔也不做；已有仓：核对可卖数量与原失效位；重仓：先控制相关性，不叠加同一风险。错过突破是等待确认的机会成本，但没有证据量化可错过的确定收益。</p>
    <h2>昨天名单与科技关注股：逐日更新，不事后改写</h2>'''
    intro += table(watch,[('name','股票'),('close','9日收盘'),('daily_change_pct','当日涨跌%'),('shape','当前形态'),('ma20','MA20'),('ma60','MA60'),('vr','量比'),('gaps','条件检查')])
    intro += '''<p>安德利9月9日收77元、涨10%，已进入过热等待，不用涨停结果倒推昨日必然可以成交；没有分钟与成交回报，不能声称按原计划已盈利。成都银行量比约0.63，转趋势观察；保留趋势不等于每日都有买点。</p><p>长飞当日约+6.04%，中际约+0.72%，新易盛约-0.38%，说明海外映射在A股也有分化。PCB中生益电子与沪电修复，但仍没有本轮定义的新买点；不能将“比昨日好”与“已满足入场”混同。</p>'''
    intro += f'<p>复核：全部候选与跟踪股合计{len(receipts)}只已逐只读取9月9日通达信原始OHLC，最新归一前复权价格差均小于0.011元。保留核对CSV；历史指标使用通过本地运营一致性验证的复权库，不是独立外部证明。</p>'
    base = (out/f'a_share_analysis_{stamp}_right_side.html').read_text(encoding='utf8')
    base = base.replace('<main>','<main>'+intro,1)
    base = base.replace('当前动作：等待候选价格与量能触发。','当前动作：观察，不可直接执行；账户和公告核查仍缺失。')
    path = out/f'a_share_analysis_{stamp}_execution_plan.html'
    path.write_text(base,encoding='utf8')
    facts = {'created_at':datetime.now().isoformat(),'a_share_asof':'2026-09-09 close','us_current_status':'2026-09-09 intraday, not final','us_snapshot_time_bj':'2026-09-09 23:41-23:42','us_intraday_pct':{'LITE':3.66313,'COHR':2.25255,'GLW':1.68113},'oil_status':'Sep 9 early snapshot, not current quote','brent_snapshot':100.72,'wti_snapshot':95.25,'coverage':summary['coverage'],'candidate_count':len(candidates),'anchor_count':len(receipts),'price_gate':gate,'status':'research only; no orders; account and announcements not verified'}
    (out/f'session_plan_{stamp}.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'report':str(path),'candidate_count':len(candidates),'anchors':len(receipts)},ensure_ascii=False))


if __name__=='__main__':
    main()
