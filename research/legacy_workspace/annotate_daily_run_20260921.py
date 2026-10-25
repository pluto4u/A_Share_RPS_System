"""Clarify the observed date mismatch in today's journal without changing gates."""
import json
from run_daily_dual_strategy import HISTORY, REPORT, render_report

history = json.loads(HISTORY.read_text(encoding="utf-8"))
entry = history[-1]
assert entry["data_asof"] == "2026-09-21"
assert entry["generated_at"] == "2026-09-21 18:14:50"
assert entry["action"] == "零交易"
assert not entry["strategy_a"] and not entry["strategy_b"]
assert entry["ecology"]["as_of_trade_date"] == "2026-09-18"
assert all(x["latest_date_tdx"] == 20260918 and x["latest_date_db"] == 20260921
           and x["mismatch_rows"] == 0 for x in entry["g0"]["details"])
entry["reason"] = (
    "同花顺库已更新至2026-09-21并通过校验，但本地TDX仍截至2026-09-18；"
    "G0因最新日期不一致未通过，G1生态也停留在9月18日而未通过日期门控。"
    "A/B未运行、发布候选均为0，未沿用旧名单。需更新通达信9月21日日K后重跑。"
)
entry["verification_note"] = {
    "db_asof": "2026-09-21", "tdx_asof": "2026-09-18",
    "g0_pass": False, "g1_pass": False,
    "sample_common_history_mismatch_rows": 0,
    "db_formula_mismatch_rows": 0,
    "meaning": "数据日期不一致，不是已证实的历史复权计算错误",
}
page = render_report(history)
notice = (
    '<section class="grid"><article class="card wide"><h2>本次新鲜度异常</h2>'
    '<p class="risk">数据库：2026-09-21；通达信及涨停生态：2026-09-18。'
    '下方情绪分数属于9月18日历史状态，不代表9月21日。G0/G1均未通过本次日期门控，'
    'A/B筛选未运行；候选计数0为门控阻断，不是正常筛选后无命中。</p>'
    '<p>共同历史样本OHLC与全库复权公式均无错配。请更新本地通达信9月21日日K后重跑，'
    '不需要为此重新初始化数据库或覆盖门控。</p></article>'
)
page = page.replace('<section class="grid">', notice, 1)
HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
REPORT.write_text(page, encoding="utf-8")
assert 'A 0 / B 0' in page and '数据截至 2026-09-21' in page
assert page.count('今天的策略A候选') == 1
print(json.dumps({"report":str(REPORT),"asof":entry["data_asof"],"A":0,"B":0,
                  "action":entry["action"],"reason":entry["reason"]},ensure_ascii=False))
