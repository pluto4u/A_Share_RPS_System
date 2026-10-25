#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
盘中极速防守监控 (Tier 1 - Intraday Emergency Monitor)
================================================================================
用途：每半小时运行一次，检测盘中突发的股灾/冰点/生态崩溃，并立即推送飞书报警。
耗时：~10-15 秒
特点：不下载全量历史数据，只拉取最新的盘口快照和昨日缓存生态数据进行对比。
"""

import sys
import time
import logging
from datetime import datetime
import pandas as pd
import akshare as ak

from project_paths import market_data_file

# 强制 UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from daily_signal import check_emergency_status
from market_gambler_watch import is_trading_session
from feishu_bot import push_text_message

def get_today_ecology_fast() -> pd.Series:
    """
    极速拉取今日生态快照 (Direct API)
    耗时: < 1秒
    """
    import urllib.request
    import json
    
    logging.info("拉取实时盘口数据 (Direct API)...")
    try:
        # 直接调东方财富高速接口，一次拉取所有 A 股核心字段
        import urllib.request
        import json
        from concurrent.futures import ThreadPoolExecutor
        
        all_data = []
        
        def fetch_page(pn):
            url = (
                f"http://push2.eastmoney.com/api/qt/clist/get"
                f"?pn={pn}&pz=100&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
                f"&fltt=2&invt=2&fid=f3"
                f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                f"&fields=f2,f3,f6"  # f2=最新价, f3=涨跌幅, f6=成交额
            )
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    page_data = json.loads(r.read())['data']
                    if page_data and 'diff' in page_data:
                        return page_data['diff']
            except Exception as e:
                logging.error(f"第 {pn} 页拉取失败: {e}")
            return []

        # 东方财富由于某些未公开限制，pz>100 时可能会截断或返回异常，我们使用更稳定的 pz=100 分60页并发拉取
        with ThreadPoolExecutor(max_workers=20) as executor:
            results = executor.map(fetch_page, range(1, 61))
            
        for res in results:
            if res:
                all_data.extend(res)
            
        spot = pd.DataFrame(all_data)
        spot = spot.rename(columns={'f2': '最新价', 'f3': '涨跌幅', 'f6': '成交额'})
        
        # 清洗数据: 剔除停牌和无涨跌幅的股票 (值为 "-")
        spot['涨跌幅'] = pd.to_numeric(spot['涨跌幅'], errors='coerce')
        spot['成交额'] = pd.to_numeric(spot['成交额'], errors='coerce')
        spot = spot.dropna(subset=['涨跌幅'])
        
    except Exception as e:
        logging.error(f"无法拉取实时盘口: {e}")
        return pd.Series()
    
    total_stocks = len(spot)
    up_count = len(spot[spot['涨跌幅'] > 0])
    down_count = len(spot[spot['涨跌幅'] < 0])
    
    # 跌停数量 (近似：跌幅 <= -9.8%)
    limit_down_count = len(spot[spot['涨跌幅'] <= -9.8])
    # 涨停数量 (近似：涨幅 >= 9.8%)
    limit_up_count = len(spot[spot['涨跌幅'] >= 9.8])
    # 大面数量 (跌幅 <= -5%)
    big_loss_count = len(spot[spot['涨跌幅'] <= -5.0])
    
    return pd.Series({
        'total_stocks': total_stocks,
        'up_count': up_count,
        'down_count': down_count,
        'limit_up_count': limit_up_count,
        'limit_down_count': limit_down_count,
        'big_loss_count': big_loss_count,
        'amount': spot['成交额'].sum() if '成交额' in spot.columns else 0
    })

def run_intraday_monitor(force=False):
    """执行盘中监控"""
    if not force and not is_trading_session():
        logging.info("当前非交易时段，跳过盘中监控")
        return
        
    logging.info("=== 开始盘中极速防守监控 ===")
    
    # 1. 拿今日实时快照
    today_eco = get_today_ecology_fast()
    if today_eco.empty:
        return
        
    # 2. 拿历史生态缓存
    try:
        history_eco = pd.read_csv(market_data_file("ecology_cache.csv"))
    except Exception as e:
        logging.warning(f"无法加载历史生态缓存 (可能尚未初始化): {e}")
        history_eco = pd.DataFrame()
        
    # 3. 运行紧急状态判定
    e_action, e_reason, e_pos = check_emergency_status(today_eco, history_eco)
    
    # 打印日志
    logging.info(f"全市场: {today_eco['total_stocks']}只")
    logging.info(f"上涨: {today_eco['up_count']}  下跌: {today_eco['down_count']}")
    logging.info(f"涨停: {today_eco['limit_up_count']}  跌停: {today_eco['limit_down_count']}  大面: {today_eco['big_loss_count']}")
    
    # 4. 如果触发报警，推送到飞书
    if e_action:
        msg = (
            f"🚨 紧急防守警报 🚨\n"
            f"触发规则: {e_action}\n"
            f"情况说明: {e_reason}\n"
            f"强制目标仓位: {int(e_pos * 100)}%\n\n"
            f"【盘口实时快照】\n"
            f"涨停数: {today_eco['limit_up_count']} | 跌停数: {today_eco['limit_down_count']}\n"
            f"大面数(<-5%): {today_eco['big_loss_count']}\n"
            f"上涨数: {today_eco['up_count']} | 下跌数: {today_eco['down_count']}\n\n"
            f"⚠️ 请立刻根据信号进行盘中减仓/清仓操作！"
        )
        logging.error(f"触发紧急警报: {e_reason}")
        push_text_message(msg)
    else:
        logging.info("风控指标正常，未触发紧急防守。")

if __name__ == '__main__':
    force_run = '--force' in sys.argv
    run_intraday_monitor(force=force_run)
