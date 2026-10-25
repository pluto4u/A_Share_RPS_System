"""Build a bounded Data Analytics artifact from the 2026-08-12 TDX screen."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
STOCKS = OUT / "ai_hardware_rotation_20260812.csv"
SECTORS = OUT / "ai_hardware_rotation_20260812_SECTORS.csv"
AUDIT = OUT / "ai_hardware_rotation_20260812_audit.json"
ARTIFACT = OUT / "ai_hardware_rotation_20260812_artifact.json"
AUDIT_CSV = OUT / "ai_hardware_rotation_20260812_AUDIT.csv"


def clean_rows(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    data = frame.loc[:, columns].copy()
    data = data.where(pd.notna(data), None)
    return data.to_dict(orient="records")


def main() -> None:
    stocks = pd.read_csv(STOCKS, encoding="utf-8-sig")
    sectors = pd.read_csv(SECTORS, encoding="utf-8-sig")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))

    names = {
        "688313.SH": "仕佳光子",
        "300620.SZ": "光库科技",
        "300570.SZ": "太辰光",
        "300308.SZ": "中际旭创",
        "300502.SZ": "新易盛",
        "600183.SH": "生益科技",
        "002463.SZ": "沪电股份",
        "603228.SH": "景旺电子",
        "300476.SZ": "胜宏科技",
        "002916.SZ": "深南电路",
        "002384.SZ": "东山精密",
        "688012.SH": "中微公司",
        "002371.SZ": "北方华创",
        "001309.SZ": "德明利",
        "300475.SZ": "香农芯创",
    }
    focus = stocks[stocks["ts_code"].isin(names)].copy()
    focus["name"] = focus["ts_code"].map(names)
    focus["status"] = "暂不博弈"
    focus.loc[focus["ts_code"].eq("002463.SZ"), "status"] = "首选条件单"
    focus.loc[focus["ts_code"].eq("300570.SZ"), "status"] = "光通信条件单"
    focus.loc[focus["ts_code"].eq("688012.SH"), "status"] = "设备备选"
    focus.loc[focus["hard_overextended"].astype(bool), "status"] = "过热不追"
    focus["volume_confirmed"] = focus["vr20_prior"] >= 1.05

    sector_rows = clean_rows(
        sectors,
        [
            "sector", "stocks", "median_ret1_pct", "median_ret5_pct",
            "median_ret20_pct", "median_vr20_prior", "breadth_positive",
            "breadth_above_ma20",
        ],
    )
    focus_rows = clean_rows(
        focus.sort_values("trade_score", ascending=False),
        [
            "sector", "ts_code", "name", "status", "close", "ret1_pct",
            "ret5_pct", "ret20_pct", "vr20_prior", "clv", "amount_yi",
            "rps5", "rps20", "rps60", "close_vs_ma20_pct", "trade_score",
            "trigger_price", "invalidation_price", "trigger_risk_pct",
            "volume_confirmed", "hard_overextended",
        ],
    )
    audit_rows = [{
        "source_rows": audit["source_rows"],
        "source_stocks": audit["source_stocks"],
        "quality_pass_stocks": audit["quality_pass_stocks"],
        "duplicate_rows": audit["duplicate_rows"],
        "invalid_rows": audit["invalid_rows"],
        "stale_codes_excluded": audit["stale_codes_excluded"],
        "pool_expected": audit["pool_expected"],
        "pool_matched": audit["pool_matched"],
        "formula_pass": audit["formula_pass"],
        "overextended": audit["overextended"],
    }]
    pd.DataFrame(audit_rows).to_csv(AUDIT_CSV, index=False, encoding="utf-8-sig")

    stocks_source = {
        "id": "tdx_stocks",
        "label": "本地通达信个股筛选结果",
        "path": "output/ai_hardware_rotation_20260812.csv",
        "query": {
            "engine": "duckdb",
            "sql": "SELECT * FROM read_csv_auto('output/ai_hardware_rotation_20260812.csv', header=true)",
            "description": "读取Python脚本基于本地通达信日线生成的44只产业候选量价明细。",
            "tables_used": ["output/ai_hardware_rotation_20260812.csv"],
            "filters": [
                "目标日=2026-08-12",
                "至少130个交易日",
                "剔除停滞代码、ST/退市风险名称和异常OHLCV",
                "候选池=光通信、PCB/材料、存储/接口、半导体设备共44只",
            ],
            "metric_definitions": [
                "VR20_prior=当日成交量/此前20日平均成交量（不含当日）",
                "CLV=(收盘价-最低价)/(最高价-最低价)",
                "RPSn=全市场n日收益率百分位",
                "过热=5日涨幅>15%，或收盘高于MA20超过10%，或单日涨幅>7%",
            ],
        },
    }
    sectors_source = {
        "id": "tdx_sectors",
        "label": "本地通达信板块汇总",
        "path": "output/ai_hardware_rotation_20260812_SECTORS.csv",
        "query": {
            "engine": "duckdb",
            "sql": "SELECT * FROM read_csv_auto('output/ai_hardware_rotation_20260812_SECTORS.csv', header=true)",
            "description": "读取四个AI硬件方向在8月12日的成分股中位数和宽度。",
            "tables_used": ["output/ai_hardware_rotation_20260812_SECTORS.csv"],
            "filters": ["目标日=2026-08-12", "成分池共44只，全部匹配"],
            "metric_definitions": [
                "板块收益率和量比均为预设成分股中位数",
                "breadth_positive=板块内当日上涨股票占比",
            ],
        },
    }
    audit_source = {
        "id": "tdx_audit",
        "label": "本地通达信数据质量审计",
        "path": "output/ai_hardware_rotation_20260812_AUDIT.csv",
        "query": {
            "engine": "duckdb",
            "sql": "SELECT * FROM read_csv_auto('output/ai_hardware_rotation_20260812_AUDIT.csv', header=true)",
            "description": "读取日期覆盖、重复、异常OHLCV、停滞代码和候选匹配审计结果。",
            "tables_used": ["output/ai_hardware_rotation_20260812_AUDIT.csv"],
            "filters": ["目标日=2026-08-12", "至少130个交易日"],
            "metric_definitions": ["quality_pass_stocks=通过窗口完整性和OHLCV合理性检查的股票数"],
        },
    }
    sources = [stocks_source, sectors_source, audit_source]

    title = "8月13日AI硬件交易筛选：光通信不弱，但买点不优"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": "基于2026年8月12日本地通达信收盘数据的量价筛选与次日博弈排序。",
        "generatedAt": "2026-08-13T00:20:00+08:00",
        "sources": sources,
        "charts": [
            {
                "id": "sector_volume",
                "title": "板块涨幅与成交量确认",
                "subtitle": "8月12日板块成分中位数；量比以此前20日均量为基准",
                "type": "scatter",
                "intent": "relationship",
                "question": "哪个板块同时具备短线强度与成交量确认？",
                "rationale": "散点同时显示1日涨幅和量比，点大小用5日涨幅补充拥挤程度。",
                "dataset": "sector_summary",
                "sourceId": "tdx_sectors",
                "encodings": {
                    "x": {"field": "median_vr20_prior", "type": "quantitative", "label": "20日量比"},
                    "y": {"field": "median_ret1_pct", "type": "quantitative", "label": "1日涨幅", "unit": "%"},
                    "size": {"field": "median_ret5_pct", "type": "quantitative", "label": "5日涨幅", "unit": "%"},
                    "label": {"field": "sector", "type": "text"},
                    "tooltip": [
                        {"field": "sector", "type": "text"},
                        {"field": "median_ret20_pct", "type": "quantitative", "unit": "%"},
                        {"field": "breadth_positive", "type": "quantitative", "format": "percent"},
                    ],
                },
                "layout": "full",
            },
            {
                "id": "focus_score",
                "title": "重点股票次日可博弈评分",
                "subtitle": "评分同时考虑5/10日反弹、20/60日趋势、量能、收盘位置、延伸度和流动性",
                "type": "bar",
                "intent": "ranking",
                "question": "重点股票中谁的次日量价条件相对更完整？",
                "rationale": "横向排序用于比较条件完整度；过热状态仍可在明细表中否决。",
                "dataset": "focus_stocks",
                "sourceId": "tdx_stocks",
                "encodings": {
                    "x": {"field": "name", "type": "nominal"},
                    "y": {"field": "trade_score", "type": "quantitative", "label": "可博弈评分"},
                    "tooltip": [
                        {"field": "status", "type": "text"},
                        {"field": "ret5_pct", "type": "quantitative", "unit": "%"},
                        {"field": "vr20_prior", "type": "quantitative"},
                        {"field": "close_vs_ma20_pct", "type": "quantitative", "unit": "%"},
                    ],
                },
                "layout": "full",
            },
        ],
        "tables": [
            {
                "id": "focus_table",
                "title": "重点个股量价审计",
                "subtitle": "触发价不是开盘买价；必须有盘中同比放量和板块共振",
                "dataset": "focus_stocks",
                "sourceId": "tdx_stocks",
                "defaultSort": {"field": "trade_score", "direction": "desc"},
                "density": "dense",
                "columns": [
                    {"field": "name", "label": "股票"},
                    {"field": "sector", "label": "板块"},
                    {"field": "status", "label": "结论"},
                    {"field": "close", "label": "收盘", "format": "number"},
                    {"field": "ret5_pct", "label": "5日%", "format": "number"},
                    {"field": "vr20_prior", "label": "量比", "format": "number"},
                    {"field": "rps20", "label": "RPS20", "format": "number"},
                    {"field": "close_vs_ma20_pct", "label": "距MA20%", "format": "number"},
                    {"field": "trade_score", "label": "评分", "format": "number"},
                    {"field": "trigger_price", "label": "触发", "format": "number"},
                    {"field": "invalidation_price", "label": "失效", "format": "number"},
                ],
                "layout": "full",
            },
            {
                "id": "audit_table",
                "title": "数据质量审计",
                "dataset": "audit",
                "sourceId": "tdx_audit",
                "defaultSort": {"field": "source_stocks", "direction": "desc"},
                "columns": [
                    {"field": "source_rows", "label": "原始行数", "format": "compact"},
                    {"field": "source_stocks", "label": "读取股票", "format": "number"},
                    {"field": "quality_pass_stocks", "label": "质量通过", "format": "number"},
                    {"field": "duplicate_rows", "label": "重复行", "format": "number"},
                    {"field": "invalid_rows", "label": "异常行", "format": "number"},
                    {"field": "stale_codes_excluded", "label": "过期代码剔除", "format": "number"},
                    {"field": "pool_matched", "label": "候选匹配", "format": "number"},
                    {"field": "formula_pass", "label": "严格通过", "format": "number"},
                ],
                "layout": "full",
            },
        ],
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {
                "id": "summary", "type": "markdown",
                "body": "## Executive Summary\n\n- **不应把A股光模块整体排除。** 8月12日光通信成分股全线上涨，中位涨幅4.82%，是四个方向里最强；问题在买点，不在产业景气。\n- **严格量价公式没有一只通过。** 44只候选中0只同时满足放量、RPS20、收盘位置和不过热。光库、仕佳已过热；中际、新易盛仍低于MA20且缩量。\n- **次日首选改为沪电股份条件单。** 它的60日相对强度高、距MA20仅6.4%、战术风险约3.9%，但量比仅0.54，所以只能等盘中放量，不能开盘直接买。\n- **若一定做光通信，太辰光只适合小仓突破确认。** 它是唯一量比超过1.05且不过热的光通信候选，但MA20仍下行、20日RPS极弱，属于高弹性反转而非主升确认。",
            },
            {
                "id": "why_not_optical", "type": "markdown",
                "body": "## 为什么不是直接追A股光模块\n\n美股光通信上涨确认了需求，但美国限制中国光收发器的政策仍处于起草/可修改阶段，市场直接交易的是Lumentum、Coherent等美国供应商的潜在替代受益，不能一比一映射到中际旭创和新易盛。更关键的是本地量价：中际、新易盛量比仅0.70/0.72，且分别低于MA20约5.7%/4.9%；仕佳、光库虽然强，却已高于MA20约21.8%/26.3%。因此当前结构呈现“两头不讨好”：大票未确认，小票已加速。",
            },
            {"id": "sector_head", "type": "markdown", "body": "## 板块强度：光通信最强，PCB最拥挤，设备最均衡"},
            {"id": "sector_chart", "type": "chart", "chartId": "sector_volume"},
            {
                "id": "stock_head", "type": "markdown",
                "body": "## 个股结论：仕佳、光库看多逻辑成立，但当前不等于可以买\n\n- **仕佳光子：过热不追。** 5日+22.1%、10日+45.7%、距MA20 +21.8%，量比只有1.00。此前用户提供的26H1收入14.95亿元、Q2扣非2.02亿元尚未在公开正式半年报中核验，因此本报告不把它当作已验证业绩。\n- **光库科技：基本面兑现、技术面过热。** 公司官方预告26H1归母净利1.40—1.50亿元、同比+170%—190%；但8月12日涨9.96%，10日+45.4%，距MA20 +26.3%，不适合T+1追涨。\n- **PCB：沪电优先，生益等待，景旺观察。** 沪电H1预告净利28.3—30亿元且泰国厂Q2转盈，量价上不热但未放量；生益5日+16.6%、距MA20 +15.4%，本地量比1.02而非1.06；景旺是趋势最强但涨停且距MA20 +24.1%。",
            },
            {"id": "focus_chart", "type": "chart", "chartId": "focus_score"},
            {"id": "focus_table_block", "type": "table", "tableId": "focus_table"},
            {
                "id": "plan", "type": "markdown",
                "body": "## 8月13日执行顺序\n\n1. **沪电股份**：不高开追。10:00后放量突破124.11元，且PCB核心股至少2/3在均价线上，才开计划仓位3%—5%；次日持续低于119.40元退出。\n2. **太辰光**：仅在光通信明显强于PCB、10:30量能进度不低于前日1.2倍，并站稳155.29元15分钟时小仓3%；跌回138.80元附近失效。其11.9%的理论触发风险仍偏大，因此不满足盘中紧止损结构就放弃。\n3. **中微公司**：设备板块共振且放量站稳386.68元时，作为第二备选；365.63元为结构失效参考。\n4. **仕佳光子、光库科技、生益科技、景旺电子**：全部进入“等缩量回踩或3—5日横盘”名单，8月13日不追。\n\n最多新增一只。若三条触发都不成立，空仓是筛选结果，不是漏选。",
            },
            {
                "id": "qa", "type": "markdown", "sourceId": "tdx_audit",
                "body": "## 数据质量与可复核性\n\n本地共读取5,272只、12,424,376行日线；对目标日前130个交易日窗口检查后，重复行0、异常OHLCV行0，剔除111个过期代码，5,161只通过质量门槛。44只预设产业候选全部匹配。量比分母使用此前20日平均量（不含8月12日），避免把当日量同时放入分母；含当日口径仅保留作交叉验证。",
            },
            {"id": "audit_table_block", "type": "table", "tableId": "audit_table"},
            {
                "id": "caveats", "type": "markdown",
                "body": "## Caveats and assumptions\n\n- 本报告基于8月12日收盘，不含8月13日盘中数据；盘中触发必须重新检查同比量能和板块宽度。\n- 用户发出分析时Coherent尚未披露FY26 Q4；其业绩在A股开盘前属于新增信息，须在盘前另行核验，不能把业绩前上涨当成业绩已验证。\n- RPS20对7月深跌后的反弹票偏苛刻，所以评分同时加入RPS5/RPS10；严格公式仍保留RPS20门槛。\n- 仕佳光子26H1数据目前按“用户提供、未获正式公告验证”处理，不用于提高评分。\n- 这是一套交易筛选与风险纪律，不保证收益；T+1下隔夜跳空可能使实际止损劣于参考价。",
            },
        ],
    }
    artifact = {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": "2026-08-13T00:20:00+08:00",
            "status": "ready",
            "datasets": {
                "sector_summary": sector_rows,
                "focus_stocks": focus_rows,
                "audit": audit_rows,
            },
        },
        "sources": sources,
    }
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(ARTIFACT)


if __name__ == "__main__":
    main()
