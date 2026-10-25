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

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime

import requests
from sentiment_engine import (
    apply_breadth_cap,
    final_position,
    score_to_position,
    volume_clearing_score,
)
from sentiment_history import save_snapshot, analyze_trend

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  配置加载                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_CONFIG = None
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feishu_config.json")


def push_text_message(text: str):
    """推送纯文本警报消息"""
    config = _load_config()
    webhook = config.get("webhook_url")
    if not webhook:
        print("[Feishu] 未配置 webhook_url，跳过推送纯文本")
        return

    payload = {"msg_type": "text", "content": {"text": text}}
    secret = config.get("secret", "")
    if secret:
        t = str(int(time.time()))
        s = f"{t}\n{secret}"
        payload["timestamp"] = t
        payload["sign"] = base64.b64encode(hmac.new(s.encode('utf-8'), b"", hashlib.sha256).digest()).decode('utf-8')

    try:
        r = requests.post(webhook, json=payload, timeout=10)
        if r.status_code == 200:
            print("[Feishu] 纯文本警报已推")
        else:
            print(f"[Feishu] 推送纯文本失败: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[Feishu] 纯文本推送异常: {e}")

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
    """
    从配置中获取值，优先级：环境变量 > 配置文件。
    环境变量名规则: FEISHU_ + key.upper()  (如 webhook_secret → FEISHU_WEBHOOK_SECRET)
    """
    # 环境变量优先 (CI/云端场景)
    env_key = f"FEISHU_{key.upper()}"
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return env_val

    cfg = _load_config()
    val = cfg.get(key, default)
    # 去除意外空白
    return val.strip() if isinstance(val, str) else val


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
    if not url or "你的HOOK_ID" in url or len(url.strip()) < 20:
        print("  [飞书] webhook_url 未配置，跳过推送")
        print("  [飞书] 请在 feishu_config.json 中填写飞书机器人 webhook 地址")
        return False

    secret = _get("webhook_secret")
    if secret:
        ts = str(int(time.time()))
        sign = _gen_sign(secret, ts)
        card_body["timestamp"] = ts
        card_body["sign"] = sign
        print(f"  [飞书] 签名: ts={ts}, secret长度={len(secret)}, sign={sign[:8]}...")
    else:
        print("  [飞书] 无签名 (webhook_secret 未配置)")

    try:
        resp = requests.post(url, json=card_body, timeout=10)
        data = resp.json()
        if data.get("code") == 0 or data.get("StatusCode") == 0:
            print("  [飞书] 卡片推送成功 ✓")
            return True
        else:
            print(f"  [飞书] 推送失败: {data}")
            if data.get("code") == 19021:
                print("  [飞书] → 签名校验失败！请确认:")
                print("  [飞书]   1. webhook_secret 是否与飞书机器人的「签名校验」密钥一致")
                print("  [飞书]   2. GitHub Secrets 中 FEISHU_WEBHOOK_SECRET 是否已设置")
                print(f"  [飞书]   3. 当前 secret 来源: {'环境变量' if os.environ.get('FEISHU_WEBHOOK_SECRET') else '配置文件'}")
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

    # ── 5. ETF 量比 (10%) — 适中最好 ──
    vol_ratio = report_data["etf_signal"].get("ratio")
    if   vol_ratio is None:                          scores["量比"] = 50
    elif 1.0 <= vol_ratio <= 1.5:                    scores["量比"] = 100
    elif 0.8 <= vol_ratio < 1.0:                     scores["量比"] = 65
    elif 1.5 < vol_ratio <= 2.0:                     scores["量比"] = 50
    else:                                            scores["量比"] = 25

    # ── 6. 中小盘量能清空 (10%) — 量能清空=卖压枯竭=底部信号 ──
    clearing_data = report_data.get("volume_clearing", [])
    vc_score, vc_desc = volume_clearing_score(clearing_data)
    scores["中小盘量能"] = vc_score

    # ── 加权求和 ──
    weights = {
        "涨跌比": 0.25, "赚钱效应": 0.20, "涨跌停比": 0.15,
        "炸板率": 0.15, "量比": 0.10, "中小盘量能": 0.15,
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
    position = score_to_position(score)
    if position == 80:
        return "80%", "强势", "积极进攻，买入强势股突破"
    if position == 60:
        return "60%", "偏强", "稳健持仓，关注回踩机会"
    if position == 30:
        return "30%", "中性", "精选个股，控制仓位"
    if position == 10:
        return "10%", "偏弱", "极度精选，防御为主"
    return "0%", "冰点", "空仓等待，不要抄底"


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
    pos_raw = int(str(pos_pct).replace("%", ""))
    trend_gate = int(report_data.get("trend_gate", 100))
    breadth_cap = int(report_data.get("breadth_cap", 100))
    if "breadth" in report_data and report_data["breadth"]:
        breadth_cap = apply_breadth_cap(
            breadth_cap,
            breadth_pct=float(report_data["breadth"].get("pct", 100.0)),
            freeze_threshold=10.0,
        )
    pos_final = final_position(pos_raw, trend_gate, breadth_cap)

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

    # ━━ Section 5: 中小盘量能清空 ━━
    clearing_data = report_data.get("volume_clearing", [])
    if clearing_data:
        vc_score, vc_desc = volume_clearing_score(clearing_data)
        vc_lines = []
        for d in clearing_data:
            if d.get("error"):
                vc_lines.append(f"  {d['name']}: 数据不足")
                continue
            status = d["status"]
            if status == "量能清空":
                icon = "🔴"
            elif status == "缩量":
                icon = "🟡"
            elif status == "放量":
                icon = "🟠"
            else:
                icon = "🟢"
            vc_lines.append(
                f"  {icon} {d['name']}  量比 **{d['vol_ratio']:.2f}x**  [{status}]"
            )
        clearing_warn = ""
        if vc_desc in ("全面清空", "部分清空"):
            clearing_warn = "\n🚨 **卖压枯竭信号！关注止跌后反弹机会**"
        vc_md = (
            f"**📉 中小盘量能** ({vc_desc})\n"
            + "\n".join(vc_lines)
            + clearing_warn
        )
        elements.append({"tag": "markdown", "content": vc_md})
        elements.append({"tag": "hr"})

    # ━━ Section 6: 广度 (完整模式) ━━
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

    # ━━ Section 7: 综合评分 & 仓位建议 ━━
    detail_lines = "  |  ".join(f"{k} {v}" for k, v in score_details.items())
    score_bar = "🟩" * (score // 10) + "⬜" * (10 - score // 10)

    verdict_md = (
        f"**📋 综合评分**\n"
        f"{score_bar}  **{score}/100**  [{pos_level}]\n\n"
        f"**建议仓位: {pos_pct}**  — {pos_advice}\n"
        f"**最终仓位: {pos_final}%**  (趋势门控 {trend_gate}% / 广度上限 {breadth_cap}%)\n\n"
        f"明细: {detail_lines}"
    )
    elements.append({"tag": "markdown", "content": verdict_md})

    # ━━ Section 8: 情绪趋势 ━━
    trend = report_data.get("_trend")
    if trend and trend.get("prev_score") is not None:
        delta = trend.get("delta", 0)
        arrow = "📈" if delta > 0 else "📉" if delta < 0 else "➡️"
        trend_parts = [f"{arrow} 较上日 **{'+' if delta > 0 else ''}{delta}分**"]

        streak = trend.get("streak", 0)
        if abs(streak) >= 2:
            s_icon = "🔥" if streak > 0 else "❄️"
            trend_parts.append(f"{s_icon} {trend['streak_label']}")

        if trend.get("ma3") is not None:
            trend_parts.append(f"3日均值 **{trend['ma3']}**")
        if trend.get("ma5") is not None:
            trend_parts.append(f"5日均值 **{trend['ma5']}**")

        trend_md = "**📊 情绪趋势**\n" + "  |  ".join(trend_parts)

        # 连跌预警
        if streak <= -3:
            trend_md += "\n🚨 **情绪连续走弱，注意防守！**"
        elif streak >= 3:
            trend_md += "\n✅ **情绪持续回暖，可适度乐观**"

        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": trend_md})

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
# ║  统一每日情绪快照                                                            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def build_sentiment_snapshot(report_data):
    """
    将 report_data 汇总为一个标准化的每日情绪快照 dict。

    返回字段至少包含:
        date, score, position_raw, trend_gate, breadth_cap, position_final
    """
    score, score_details = calculate_sentiment_score(report_data)
    pos_pct, pos_level, pos_advice = get_position_advice(score)
    pos_raw = int(str(pos_pct).replace("%", ""))

    trend_gate = int(report_data.get("trend_gate", 100))
    breadth_cap_val = int(report_data.get("breadth_cap", 100))
    if "breadth" in report_data and report_data["breadth"]:
        breadth_cap_val = apply_breadth_cap(
            breadth_cap_val,
            breadth_pct=float(report_data["breadth"].get("pct", 100.0)),
            freeze_threshold=10.0,
        )
    pos_final = final_position(pos_raw, trend_gate, breadth_cap_val)

    return {
        "date": report_data.get("timestamp", ""),
        "score": score,
        "score_details": score_details,
        "position_raw": pos_raw,
        "position_level": pos_level,
        "position_advice": pos_advice,
        "trend_gate": trend_gate,
        "breadth_cap": breadth_cap_val,
        "position_final": pos_final,
    }


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
    pos_raw = int(str(pos_pct).replace("%", ""))
    trend_gate = int(report_data.get("trend_gate", 100))
    breadth_cap = int(report_data.get("breadth_cap", 100))
    if "breadth" in report_data and report_data["breadth"]:
        breadth_cap = apply_breadth_cap(
            breadth_cap,
            breadth_pct=float(report_data["breadth"].get("pct", 100.0)),
            freeze_threshold=10.0,
        )
    pos_final = final_position(pos_raw, trend_gate, breadth_cap)
    print(
        f"  [情绪评分] {score}/100  [{pos_level}]  建议仓位 {pos_pct}  |  "
        f"最终仓位 {pos_final}%"
    )

    # 2. 保存情绪历史 & 趋势分析
    ts_raw = report_data.get("timestamp", "")
    # 提取日期: "2026-02-14 (收盘数据)" → "2026-02-14"
    date_part = ts_raw[:10] if len(ts_raw) >= 10 else datetime.now().strftime("%Y-%m-%d")
    time_part = datetime.now().strftime("%H:%M")
    try:
        save_snapshot(
            date_str=date_part,
            time_str=time_part,
            score=score,
            position=pos_final,
            status=report_data.get("status", ""),
            score_details=score_details,
        )
        print(f"  [历史] 已记录: {date_part} {time_part} → {score}分 / {pos_final}%")
    except Exception as e:
        print(f"  [历史] 保存失败: {e}")

    trend = analyze_trend()
    if trend.get("summary"):
        print(f"  [趋势] {trend['summary']}")

    # 注入趋势到 report_data 供卡片使用
    report_data["_trend"] = trend

    # 3. 推送 Webhook 卡片
    card = build_market_card(report_data)
    send_card(card)

    # 4. 写入多维表格
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


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  消息卡片构建 — 今日操作指令/实盘信号                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def build_daily_action_card(sig, holdings, scores):
    """
    构建今日实盘信号的飞书交互式卡片 — 风格与 build_market_card 完全一致。
    """
    from daily_signal import score_to_target, score_to_zone

    ts = sig['date']
    score = sig['score']
    zone_cn = sig['zone_cn']
    action = sig['action']
    cur_pct = int(sig['current_pos'] * 100)
    tgt_pct = int(sig['target_pos'] * 100)

    if   score >= 70: template = "green"
    elif score >= 40: template = "blue"
    elif score >= 20: template = "orange"
    else:             template = "red"

    elements = []

    # ━━ Section 1: 情绪得分 ━━
    score_bar = "🟩" * (int(score) // 10) + "⬜" * (10 - int(score) // 10)

    if   score >= 87.5: zone_tag = "🔴过热"
    elif score >= 62.5: zone_tag = "🟠偏热"
    elif score >= 37.5: zone_tag = "🟡正常"
    elif score >= 12.5: zone_tag = "🟢偏冷"
    else:               zone_tag = "🟢极冷"

    chg_1d_arrow = "↑" if sig['chg_1d'] > 0 else "↓" if sig['chg_1d'] < 0 else "→"

    score_md = (
        f"**🎯 情绪得分**\n"
        f"{score_bar}  **{score:.1f}/100**  [{zone_cn}]\n"
        f"变化: {chg_1d_arrow}**{sig['chg_1d']:+.1f}**pts (1日)，"
        f"**{sig['chg_3d']:+.1f}**pts (3日)"
    )
    elements.append({"tag": "markdown", "content": score_md})
    elements.append({"tag": "hr"})

    # ━━ Section 2: 仓位建议 ━━
    pos_bar_cur = "🟩" * (cur_pct // 10) + "⬜" * (10 - cur_pct // 10)
    pos_bar_tgt = "🟦" * (tgt_pct // 10) + "⬜" * (10 - tgt_pct // 10)

    pos_md = (
        f"**📊 仓位建议**\n"
        f"当前持仓: {pos_bar_cur}  **{cur_pct}%**\n"
        f"目标仓位: {pos_bar_tgt}  **{tgt_pct}%**  (情绪分 → 25% 步进)\n\n"
        f"距上次调仓: **{sig['days_since_change']}** 天"
        f"  (上次: {sig['last_change_date'] or '无'}"
        f"  {int(sig['last_change_from']*100)}%→{int(sig['last_change_to']*100)}%)"
    )
    if sig['days_until_next'] > 0:
        pos_md += f"\n冷却期剩余: **{sig['days_until_next']}** 天"
    elements.append({"tag": "markdown", "content": pos_md})
    elements.append({"tag": "hr"})

    # ━━ Section 3: 操作建议 ━━
    if action == 'ADJUST':
        direction = "加仓" if tgt_pct > cur_pct else "减仓"
        delta = abs(tgt_pct - cur_pct)
        action_icon = "🔺" if tgt_pct > cur_pct else "🔻"
        action_detail = (
            f"{action_icon} **{direction}操作**: {cur_pct}% → {tgt_pct}%  (变动 {delta}%)"
        )
    elif action == 'EMERGENCY':
        action_detail = f"🚨 **紧急干预!**\n{sig['action_cn']}"
    elif action == 'PENDING':
        action_detail = (
            f"⏳ **待调仓**: 目标 {tgt_pct}%，"
            f"还需等 **{sig['days_until_next']}** 天冷却期 (防止频繁换仓)"
        )
    elif action == 'CAUTION':
        action_detail = f"⚠️ **高弹性区间**: 正常持仓 {cur_pct}%，注意随时兑现"
    else:
        action_detail = f"✅ **无需操作**: 持仓维持 **{cur_pct}%**"

    elements.append({
        "tag": "markdown",
        "content": f"**💡 操作建议**\n{action_detail}",
    })
    elements.append({"tag": "hr"})

    # ━━ Section 4: ETF 轮动持仓 ━━
    if holdings:
        total_capital = 1_000_000
        target_invested = total_capital * tgt_pct / 100

        etf_lines = []
        for h in holdings:
            sym = h['symbol']
            name = h.get('name', sym)
            weight = h.get('weight', 1.0 / len(holdings))
            mom = h.get('momentum_score', 0)
            amount = target_invested * weight
            etf_lines.append(
                f"  {sym}  {name:<10}  "
                f"配比{weight*100:>3.0f}%  "
                f"动量{mom:>+6.1f}%  "
                f"约{amount/10000:.1f}万"
            )

        cash = total_capital - target_invested
        etf_md = (
            f"**🛒 ETF 轮动持仓** (假设总资金 100 万)\n"
            f"```\n" + "\n".join(etf_lines) + f"\n```\n"
            f"现金留存: **{cash/10000:.1f}** 万 ({100 - tgt_pct}%)"
        )
        elements.append({"tag": "markdown", "content": etf_md})
        elements.append({"tag": "hr"})

    # ━━ Section 5: 近 10 日情绪趋势 ━━
    recent = scores.tail(10)
    trend_lines = []
    for _, row in recent.iterrows():
        d = row['trade_date'].strftime('%m-%d')
        s = row['emotion_score']
        z = score_to_zone(s)[1]
        t = int(score_to_target(s) * 100)
        mini_bar = "█" * int(s / 10) + "░" * (10 - int(s / 10))
        marker = "  ← 今日" if d == ts[-5:] else ""
        trend_lines.append(
            f"  {d}  {s:>5.1f}  {z:<4}  {t:>3}%  {mini_bar}{marker}"
        )

    elements.append({
        "tag": "markdown",
        "content": (
            f"**📅 近 10 日情绪趋势**\n"
            f"```\n"
            f"  日期   分数  档位  目标仓  迷你图\n"
            + "\n".join(trend_lines)
            + "\n```"
        ),
    })

    # ━━ 底部备注 ━━
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": (
                f"今日实盘信号 v2.0 | S5 线性防抖策略 (Sharpe 1.34 / 最大回撤 -9.2%) | "
                f"{ts} | 14:50-14:57 完成操作"
            ),
        }],
    })

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"⚡ 今日实盘信号 ({ts})",
                },
                "template": template,
            },
            "elements": elements,
        },
    }
    return card


def push_daily_action_report(sig, holdings, scores):
    """一键推送今日操作指令卡片"""
    print("\n  ── 飞书推送 (实盘信号卡片) ──────────────────")
    card = build_daily_action_card(sig, holdings, scores)
    send_card(card)
    print("  ────────────────────────────────────────────")
