"""
================================================================================
飞书机器人 + 多维表格 集成模块  v1.0       feishu_bot.py
================================================================================
功能:
  1. Webhook 消息卡片  — 向飞书群推送带颜色标记的交互式卡片
  2. 情绪评分系统       — 将多维市场指标量化为 0-100 分
  3. 多维表格 (Bitable) — 自动将选股/情绪数据写入飞书多维表格

配置:
  在同目录 feishu_config.json 中填写 webhook_url 等参数即可。

依赖:
  requests (akshare 已自带)

================================================================================
"""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime

import requests

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  配置加载                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_CONFIG = None
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feishu_config.json")


def _load_config():
    """加载 feishu_config.json，缓存到模块级变量"""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    if not os.path.exists(_CONFIG_FILE):
        print(f"  [飞书] 配置文件不存在: {_CONFIG_FILE}")
        print(f"  [飞书] 请复制 feishu_config.json 并填写你的配置")
        _CONFIG = {}
        return _CONFIG

    with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
        _CONFIG = json.load(f)
    return _CONFIG


def _get(key, default=""):
    """从配置中获取值"""
    cfg = _load_config()
    return cfg.get(key, default)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  Webhook 签名 & 发送                                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _gen_sign(secret, timestamp):
    """生成飞书 Webhook 签名 (HMAC-SHA256 + Base64)"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_card(card_body):
    """
    通过 Webhook 向飞书群发送消息卡片。

    Parameters
    ----------
    card_body : dict
        完整的 {"msg_type": "interactive", "card": {...}} JSON

    Returns
    -------
    bool
        发送是否成功
    """
    url = _get("webhook_url")
    if not url or "你的HOOK_ID" in url:
        print("  [飞书] webhook_url 未配置，跳过推送")
        return False

    secret = _get("webhook_secret")
    if secret:
        ts = str(int(time.time()))
        card_body["timestamp"] = ts
        card_body["sign"] = _gen_sign(secret, ts)

    try:
        resp = requests.post(url, json=card_body, timeout=10)
        data = resp.json()
        if data.get("code") == 0 or data.get("StatusCode") == 0:
            print("  [飞书] 卡片推送成功 ✓")
            return True
        else:
            print(f"  [飞书] 推送失败: {data}")
            return False
    except Exception as e:
        print(f"  [飞书] 推送异常: {e}")
        return False


def send_text(text):
    """发送纯文本消息（简单场景）"""
    url = _get("webhook_url")
    if not url or "你的HOOK_ID" in url:
        return False

    body = {"msg_type": "text", "content": {"text": text}}
    secret = _get("webhook_secret")
    if secret:
        ts = str(int(time.time()))
        body["timestamp"] = ts
        body["sign"] = _gen_sign(secret, ts)

    try:
        resp = requests.post(url, json=body, timeout=10)
        return resp.json().get("code") == 0
    except Exception:
        return False


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  情绪评分系统 (0-100)                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def calculate_sentiment_score(report_data):
    """
    将多维市场指标量化为 0-100 的综合情绪分。

    权重分配:
        涨跌比         25%
        赚钱效应       25%
        涨停/跌停比    20%
        炸板率(逆向)   15%
        ETF量比        15%

    Parameters
    ----------
    report_data : dict
        包含 all_spot(DataFrame), zt_pools(dict), money_effect(float|None),
        etf_signal(dict) 的数据字典

    Returns
    -------
    (int, dict)
        总分, 各指标得分明细
    """
    scores = {}

    # ── 1. 涨跌比 (25%) ──
    changes = report_data["all_spot"]["涨跌幅"].dropna()
    up_n = int((changes > 0).sum())
    down_n = int((changes < 0).sum())
    ratio = up_n / down_n if down_n > 0 else 5.0

    if   ratio > 3:   scores["涨跌比"] = 100
    elif ratio > 2:   scores["涨跌比"] = 85
    elif ratio > 1.5: scores["涨跌比"] = 75
    elif ratio > 1:   scores["涨跌比"] = 50
    elif ratio > 0.5: scores["涨跌比"] = 25
    else:             scores["涨跌比"] = 0

    # ── 2. 赚钱效应 (25%) ──
    avg_ret = report_data.get("money_effect")
    if   avg_ret is None:  scores["赚钱效应"] = 50
    elif avg_ret > 3:      scores["赚钱效应"] = 100
    elif avg_ret > 1:      scores["赚钱效应"] = 75
    elif avg_ret > 0:      scores["赚钱效应"] = 50
    elif avg_ret > -2:     scores["赚钱效应"] = 25
    else:                  scores["赚钱效应"] = 0

    # ── 3. 涨停/跌停比 (20%) ──
    zt_n = report_data["zt_pools"]["zt_n"]
    dt_n = report_data["zt_pools"]["dt_n"]
    zt_ratio = zt_n / dt_n if dt_n > 0 else 10

    if   zt_ratio > 5:   scores["涨跌停比"] = 100
    elif zt_ratio > 3:   scores["涨跌停比"] = 75
    elif zt_ratio > 1.5: scores["涨跌停比"] = 50
    elif zt_ratio > 1:   scores["涨跌停比"] = 25
    else:                scores["涨跌停比"] = 0

    # ── 4. 炸板率 (15%) — 越低越好 ──
    zb_n = report_data["zt_pools"]["zb_n"]
    total_board = zt_n + zb_n
    zb_rate = zb_n / total_board * 100 if total_board > 0 else 50

    if   zb_rate < 15: scores["炸板率"] = 100
    elif zb_rate < 25: scores["炸板率"] = 75
    elif zb_rate < 35: scores["炸板率"] = 50
    elif zb_rate < 50: scores["炸板率"] = 25
    else:              scores["炸板率"] = 0

    # ── 5. ETF 量比 (15%) — 适中最好 ──
    vol_ratio = report_data["etf_signal"].get("ratio")
    if   vol_ratio is None:                          scores["量比"] = 50
    elif 1.0 <= vol_ratio <= 1.5:                    scores["量比"] = 100
    elif 0.8 <= vol_ratio < 1.0:                     scores["量比"] = 65
    elif 1.5 < vol_ratio <= 2.0:                     scores["量比"] = 50
    else:                                            scores["量比"] = 25

    # ── 加权求和 ──
    weights = {
        "涨跌比": 0.25, "赚钱效应": 0.25, "涨跌停比": 0.20,
        "炸板率": 0.15, "量比": 0.15,
    }
    total = sum(scores[k] * weights[k] for k in weights)
    return round(total), scores


def get_position_advice(score):
    """
    情绪分 → 仓位建议映射

    Returns
    -------
    (str, str, str)
        仓位百分比, 判定, 建议
    """
    if   score >= 80: return "80%", "强势", "积极进攻，买入强势股突破"
    elif score >= 60: return "60%", "偏强", "稳健持仓，关注回踩机会"
    elif score >= 40: return "30%", "中性", "精选个股，控制仓位"
    elif score >= 20: return "10%", "偏弱", "极度精选，防御为主"
    else:             return "0%",  "冰点", "空仓等待，不要抄底"


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  消息卡片构建 — 市场情绪                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def build_market_card(report_data):
    """
    构建市场情绪监控的飞书交互式卡片。

    Parameters
    ----------
    report_data : dict
        {
            "timestamp": "2026-02-14 15:35",
            "status": "盘后",
            "idx_data": [...],
            "all_spot": DataFrame,
            "zt_pools": {"zt_n":..., "dt_n":..., "zb_n":..., "zt_df":...},
            "money_effect": float|None,
            "etf_signal": {"vol_today":..., "vol_avg5":..., "ratio":...},
            "breadth": {"pct":..., "above":..., "total":...} | None
        }

    Returns
    -------
    dict
        完整的卡片 JSON，可直接传给 send_card()
    """
    score, score_details = calculate_sentiment_score(report_data)
    pos_pct, pos_level, pos_advice = get_position_advice(score)

    # ── 卡片颜色 ──
    if   score >= 70: template = "green"
    elif score >= 40: template = "blue"
    elif score >= 20: template = "orange"
    else:             template = "red"

    ts = report_data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    status = report_data.get("status", "")

    elements = []

    # ━━ Section 1: 指数总览 ━━
    idx_lines = []
    for d in report_data.get("idx_data", []):
        if d.get("error"):
            continue
        arrow = "🔺" if d["change_pct"] > 0 else "🔻" if d["change_pct"] < 0 else "➖"
        ma_icon = "✅" if d["ma20_status"] == "站上" else "⚠️"
        vr = d.get("vol_ratio", 0)
        vr_tag = f"  量比{vr:.1f}" if vr > 1.5 else ""
        idx_lines.append(
            f"  {d['name']:<6} {d['close']:>10.2f}  "
            f"{arrow} {d['change_pct']:+.2f}%  "
            f"MA20 {ma_icon}{vr_tag}"
        )

    elements.append({
        "tag": "markdown",
        "content": "**📈 指数总览**\n```\n" + "\n".join(idx_lines) + "\n```",
    })
    elements.append({"tag": "hr"})

    # ━━ Section 2: 情绪面板 ━━
    changes = report_data["all_spot"]["涨跌幅"].dropna()
    total_n = len(changes)
    up_n = int((changes > 0).sum())
    down_n = int((changes < 0).sum())
    flat_n = int((changes == 0).sum())
    ratio = up_n / down_n if down_n > 0 else float("inf")

    zt_n = report_data["zt_pools"]["zt_n"]
    dt_n = report_data["zt_pools"]["dt_n"]
    zb_n = report_data["zt_pools"]["zb_n"]
    total_board = zt_n + zb_n
    zb_rate = zb_n / total_board * 100 if total_board > 0 else 0

    if   ratio > 3:    ad_tag = "🟢极强"
    elif ratio > 1.5:  ad_tag = "🟢偏强"
    elif ratio > 0.8:  ad_tag = "🟡均衡"
    elif ratio > 0.3:  ad_tag = "🟠偏弱"
    else:              ad_tag = "🔴极弱"

    sentiment_md = (
        f"**🎯 情绪面板**\n"
        f"上涨 **{up_n}** ({up_n/total_n*100:.1f}%)  |  "
        f"下跌 **{down_n}** ({down_n/total_n*100:.1f}%)  |  "
        f"平盘 {flat_n}\n"
        f"涨跌比: **{ratio:.2f}**  {ad_tag}\n\n"
        f"涨停 **{zt_n}**  |  跌停 **{dt_n}**  |  "
        f"炸板 **{zb_n}** (炸板率 {zb_rate:.1f}%)"
    )
    elements.append({"tag": "markdown", "content": sentiment_md})
    elements.append({"tag": "hr"})

    # ━━ Section 3: 赚钱效应 ━━
    avg_ret = report_data.get("money_effect")
    if avg_ret is not None:
        if   avg_ret > 2:   ret_tag = "🟢追板大赚"
        elif avg_ret > 0:   ret_tag = "🟢追板有肉"
        elif avg_ret > -1:  ret_tag = "🟡追板微亏"
        else:               ret_tag = "🔴追板大亏"
        money_md = (
            f"**💰 赚钱效应** (昨日涨停 → 今日表现)\n"
            f"平均收益: **{avg_ret:+.2f}%**  {ret_tag}"
        )
    else:
        money_md = "**💰 赚钱效应**\n数据暂不可用"
    elements.append({"tag": "markdown", "content": money_md})
    elements.append({"tag": "hr"})

    # ━━ Section 4: ETF 庄家信号 ━━
    etf = report_data.get("etf_signal", {})
    vol_ratio = etf.get("ratio")
    if vol_ratio is not None:
        alert = "⚠️ 放量异常!" if vol_ratio > 1.5 else "✅ 正常"
        etf_md = (
            f"**🔍 庄家信号** (510300 ETF)\n"
            f"量比: **{vol_ratio:.2f}x**  {alert}"
        )
    else:
        etf_md = "**🔍 庄家信号**\n数据暂不可用"
    elements.append({"tag": "markdown", "content": etf_md})
    elements.append({"tag": "hr"})

    # ━━ Section 5: 广度 (完整模式) ━━
    breadth = report_data.get("breadth")
    if breadth:
        pct = breadth["pct"]
        freeze_warn = "\n🚨 **冰点行情！极端缩量区域，关注超跌反弹**" if pct < 10 else ""
        elements.append({
            "tag": "markdown",
            "content": (
                f"**📊 市场广度** (站上 MA20 占比)\n"
                f"**{pct:.1f}%** ({breadth['above']}/{breadth['total']})"
                f"{freeze_warn}"
            ),
        })
        elements.append({"tag": "hr"})

    # ━━ Section 6: 综合评分 & 仓位建议 ━━
    detail_lines = "  |  ".join(f"{k} {v}" for k, v in score_details.items())
    score_bar = "🟩" * (score // 10) + "⬜" * (10 - score // 10)

    verdict_md = (
        f"**📋 综合评分**\n"
        f"{score_bar}  **{score}/100**  [{pos_level}]\n\n"
        f"**建议仓位: {pos_pct}**  — {pos_advice}\n\n"
        f"明细: {detail_lines}"
    )
    elements.append({"tag": "markdown", "content": verdict_md})

    # ━━ 底部备注 ━━
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": f"A股情绪监控 v2.0 | {ts} [{status}] | 数据来源: akshare",
        }],
    })

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 A股情绪监控  |  {ts}  [{status}]",
                },
                "template": template,
            },
            "elements": elements,
        },
    }
    return card


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  消息卡片构建 — RPS 选股结果                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def build_rps_card(report_data):
    """
    构建 RPS 选股结果的飞书交互式卡片。

    Parameters
    ----------
    report_data : dict
        {
            "timestamp": "...",
            "market_safe": True/False,
            "total_stocks": int,
            "filtered_stocks": int,
            "selected_df": DataFrame (含 code, name, industry, rps_50, rps_120, ...),
            "industry_summary": dict (行业 → 数量),
        }
    """
    ts = report_data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
    is_safe = report_data.get("market_safe", False)

    if is_safe:
        template = "green"
        timing_icon = "✅"
        timing_text = "安全 — 大盘趋势健康"
    else:
        template = "red"
        timing_icon = "🚫"
        timing_text = "危险 — 大盘破位，建议空仓"

    elements = []

    # ━━ 大盘风控 ━━
    elements.append({
        "tag": "markdown",
        "content": f"**🛡️ 大盘风控**\n{timing_icon} {timing_text}",
    })
    elements.append({"tag": "hr"})

    if not is_safe:
        elements.append({
            "tag": "markdown",
            "content": "⚠️ **大盘破位，选股流程已终止。建议空仓等待右侧信号。**",
        })
        return _wrap_card(
            f"🛡️ RPS选股 | {ts} | 空仓信号", "red", elements, ts
        )

    # ━━ 清洗概览 ━━
    total = report_data.get("total_stocks", 0)
    filtered = report_data.get("filtered_stocks", 0)
    selected_df = report_data.get("selected_df")
    selected_n = len(selected_df) if selected_df is not None and not selected_df.empty else 0

    elements.append({
        "tag": "markdown",
        "content": (
            f"**📊 选股漏斗**\n"
            f"全市场 **{total}** 只 → 清洗后 **{filtered}** 只 → "
            f"入选 **{selected_n}** 只"
        ),
    })
    elements.append({"tag": "hr"})

    # ━━ Top 选股列表 ━━
    if selected_df is not None and not selected_df.empty:
        show_n = min(15, len(selected_df))
        stock_lines = []
        for i, (_, row) in enumerate(selected_df.head(show_n).iterrows(), 1):
            code = row.get("股票代码", row.get("code", ""))
            name = row.get("股票名称", row.get("name", ""))
            industry = row.get("所属行业", row.get("industry", ""))
            rps50 = row.get("RPS_50日", row.get("rps_50", 0))
            rps120 = row.get("RPS_120日", row.get("rps_120", 0))
            ret50 = row.get("50日涨跌幅(%)", row.get("return_50d", 0))
            stock_lines.append(
                f"  {i:>2}. {code} {name:<6}  "
                f"RPS50={rps50:>5.1f}  RPS120={rps120:>5.1f}  "
                f"50日{ret50:+.1f}%  [{industry}]"
            )

        if len(selected_df) > show_n:
            stock_lines.append(f"  ... 还有 {len(selected_df) - show_n} 只，详见多维表格")

        elements.append({
            "tag": "markdown",
            "content": "**🏆 入选股票 (按 RPS50 降序)**\n```\n" + "\n".join(stock_lines) + "\n```",
        })
        elements.append({"tag": "hr"})

        # ━━ 行业分布 ━━
        industry_summary = report_data.get("industry_summary", {})
        if industry_summary:
            top_industries = sorted(industry_summary.items(), key=lambda x: x[1], reverse=True)[:8]
            ind_parts = [f"{name} **{cnt}**只" for name, cnt in top_industries]
            elements.append({
                "tag": "markdown",
                "content": "**🏭 行业分布**\n" + "  |  ".join(ind_parts),
            })
        else:
            elements.append({
                "tag": "markdown",
                "content": "**🏭 行业分布**\n暂无行业数据",
            })
    else:
        elements.append({
            "tag": "markdown",
            "content": "📭 本期无符合条件的股票",
        })

    return _wrap_card(
        f"🏆 RPS选股结果 | {ts} | 入选{selected_n}只",
        template, elements, ts,
    )


def _wrap_card(title, template, elements, ts):
    """组装完整卡片 JSON"""
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": f"RPS选股系统 v1.0 | {ts} | 数据来源: akshare",
        }],
    })
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "elements": elements,
        },
    }


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  多维表格 (Bitable) — 认证                                                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_TENANT_TOKEN = None
_TENANT_TOKEN_EXPIRE = 0


def _get_tenant_token():
    """
    获取飞书 tenant_access_token (有效期 2 小时，自动缓存)。
    需要 feishu_config.json 中配置 app_id 和 app_secret。
    """
    global _TENANT_TOKEN, _TENANT_TOKEN_EXPIRE

    if _TENANT_TOKEN and time.time() < _TENANT_TOKEN_EXPIRE:
        return _TENANT_TOKEN

    app_id = _get("app_id")
    app_secret = _get("app_secret")
    if not app_id or not app_secret:
        print("  [飞书] app_id / app_secret 未配置，多维表格功能不可用")
        return None

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(url, json={
            "app_id": app_id,
            "app_secret": app_secret,
        }, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            _TENANT_TOKEN = data["tenant_access_token"]
            _TENANT_TOKEN_EXPIRE = time.time() + data.get("expire", 7200) - 60
            return _TENANT_TOKEN
        else:
            print(f"  [飞书] 获取 token 失败: {data.get('msg')}")
            return None
    except Exception as e:
        print(f"  [飞书] 获取 token 异常: {e}")
        return None


def _bitable_headers():
    """构造 Bitable API 请求头"""
    token = _get_tenant_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  多维表格 (Bitable) — 数据操作                                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_BITABLE_BASE = "https://open.feishu.cn/open-apis/bitable/v1"


def _list_records(app_token, table_id, page_size=500):
    """列出表中现有记录（用于清理旧数据）"""
    headers = _bitable_headers()
    if not headers:
        return []

    url = f"{_BITABLE_BASE}/apps/{app_token}/tables/{table_id}/records"
    records = []
    page_token = None

    while True:
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                print(f"  [飞书] 列出记录失败: {data.get('msg')}")
                break
            items = data.get("data", {}).get("items", [])
            records.extend(items)
            if not data.get("data", {}).get("has_more", False):
                break
            page_token = data["data"].get("page_token")
        except Exception as e:
            print(f"  [飞书] 列出记录异常: {e}")
            break

    return records


def _batch_delete_records(app_token, table_id, record_ids):
    """批量删除记录"""
    if not record_ids:
        return
    headers = _bitable_headers()
    if not headers:
        return

    url = f"{_BITABLE_BASE}/apps/{app_token}/tables/{table_id}/records/batch_delete"
    # 每次最多 500 条
    for i in range(0, len(record_ids), 500):
        batch = record_ids[i:i + 500]
        try:
            requests.post(url, headers=headers, json={"records": batch}, timeout=15)
        except Exception:
            pass


def _batch_create_records(app_token, table_id, fields_list):
    """
    批量写入记录。

    Parameters
    ----------
    fields_list : list[dict]
        每个 dict 是一条记录的字段 {字段名: 值}
    """
    headers = _bitable_headers()
    if not headers:
        return False

    url = f"{_BITABLE_BASE}/apps/{app_token}/tables/{table_id}/records/batch_create"

    # 每批最多 500 条
    success = True
    for i in range(0, len(fields_list), 500):
        batch = fields_list[i:i + 500]
        body = {"records": [{"fields": f} for f in batch]}
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=30)
            data = resp.json()
            if data.get("code") != 0:
                print(f"  [飞书] 批量写入失败: {data.get('msg')}")
                success = False
        except Exception as e:
            print(f"  [飞书] 批量写入异常: {e}")
            success = False

    return success


def update_bitable_rps(selected_df):
    """
    将 RPS 选股结果写入飞书多维表格（先清空旧数据再写入）。

    多维表格需要预先创建以下字段:
        日期(文本), 股票代码(文本), 股票名称(文本), 所属行业(文本),
        RPS_50日(数字), RPS_120日(数字), 50日涨跌幅(数字), 120日涨跌幅(数字)

    Parameters
    ----------
    selected_df : pd.DataFrame
        最终入选的股票列表
    """
    app_token = _get("bitable_app_token")
    table_id = _get("bitable_table_id_rps")

    if not app_token or not table_id:
        print("  [飞书] 多维表格 RPS 配置缺失，跳过")
        return

    print("  [飞书] 正在更新多维表格 (RPS) ...")

    # 清除旧数据
    old_records = _list_records(app_token, table_id)
    if old_records:
        old_ids = [r["record_id"] for r in old_records]
        _batch_delete_records(app_token, table_id, old_ids)
        print(f"  [飞书] 清除旧记录 {len(old_ids)} 条")

    if selected_df is None or selected_df.empty:
        print("  [飞书] 无选股数据，跳过写入")
        return

    # 构造记录
    today = datetime.now().strftime("%Y-%m-%d")
    fields_list = []
    for _, row in selected_df.iterrows():
        code = row.get("股票代码", row.get("code", ""))
        name = row.get("股票名称", row.get("name", ""))
        industry = row.get("所属行业", row.get("industry", ""))
        rps50 = float(row.get("RPS_50日", row.get("rps_50", 0)))
        rps120 = float(row.get("RPS_120日", row.get("rps_120", 0)))
        ret50 = float(row.get("50日涨跌幅(%)", row.get("return_50d", 0)))
        ret120 = float(row.get("120日涨跌幅(%)", row.get("return_120d", 0)))

        fields_list.append({
            "日期": today,
            "股票代码": str(code),
            "股票名称": str(name),
            "所属行业": str(industry),
            "RPS_50日": round(rps50, 2),
            "RPS_120日": round(rps120, 2),
            "50日涨跌幅": round(ret50, 2),
            "120日涨跌幅": round(ret120, 2),
        })

    ok = _batch_create_records(app_token, table_id, fields_list)
    if ok:
        print(f"  [飞书] 多维表格写入 {len(fields_list)} 条记录 ✓")


def update_bitable_sentiment(report_data, score, score_details):
    """
    将情绪监控快照追加到飞书多维表格（不清空，每天追加一行）。

    多维表格需要预先创建以下字段:
        日期(文本), 情绪评分(数字), 建议仓位(文本),
        涨跌比(数字), 涨停数(数字), 跌停数(数字),
        炸板率(数字), 赚钱效应(数字)
    """
    app_token = _get("bitable_app_token")
    table_id = _get("bitable_table_id_sentiment")

    if not app_token or not table_id:
        return

    changes = report_data["all_spot"]["涨跌幅"].dropna()
    up_n = int((changes > 0).sum())
    down_n = int((changes < 0).sum())
    ratio = round(up_n / down_n, 2) if down_n > 0 else 99.0

    zt_n = report_data["zt_pools"]["zt_n"]
    dt_n = report_data["zt_pools"]["dt_n"]
    zb_n = report_data["zt_pools"]["zb_n"]
    zb_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) > 0 else 0.0
    avg_ret = report_data.get("money_effect")

    pos_pct, _, _ = get_position_advice(score)

    fields = {
        "日期": datetime.now().strftime("%Y-%m-%d"),
        "情绪评分": score,
        "建议仓位": pos_pct,
        "涨跌比": ratio,
        "涨停数": zt_n,
        "跌停数": dt_n,
        "炸板率": zb_rate,
        "赚钱效应": round(avg_ret, 2) if avg_ret is not None else 0.0,
    }

    _batch_create_records(app_token, table_id, [fields])
    print("  [飞书] 情绪快照已追加到多维表格 ✓")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  高层调用接口 — 一键推送                                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def push_market_report(report_data):
    """
    一键推送市场情绪报告:  Webhook 卡片 + 多维表格

    Parameters
    ----------
    report_data : dict
        由 market_gambler_watch.py 收集的完整数据
    """
    print("\n  ── 飞书推送 ──────────────────────────────────")

    # 1. 计算情绪评分
    score, score_details = calculate_sentiment_score(report_data)
    pos_pct, pos_level, _ = get_position_advice(score)
    print(f"  [情绪评分] {score}/100  [{pos_level}]  建议仓位 {pos_pct}")

    # 2. 推送 Webhook 卡片
    card = build_market_card(report_data)
    send_card(card)

    # 3. 写入多维表格
    update_bitable_sentiment(report_data, score, score_details)

    print("  ────────────────────────────────────────────")


def push_rps_report(report_data, selected_output_df=None):
    """
    一键推送 RPS 选股结果:  Webhook 卡片 + 多维表格

    Parameters
    ----------
    report_data : dict
        由 rps_system.py 收集的选股数据
    selected_output_df : pd.DataFrame | None
        最终的输出表 (含中文列名)
    """
    print("\n  ── 飞书推送 ──────────────────────────────────")

    # 1. 推送 Webhook 卡片
    card = build_rps_card(report_data)
    send_card(card)

    # 2. 写入多维表格
    df = selected_output_df if selected_output_df is not None else report_data.get("selected_df")
    update_bitable_rps(df)

    print("  ────────────────────────────────────────────")
