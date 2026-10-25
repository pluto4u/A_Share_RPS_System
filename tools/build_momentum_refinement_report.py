"""Build a validated Data Analytics artifact for the momentum-view refinement."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def records(df: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    if columns is not None:
        df = df[columns]
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def main() -> None:
    refined = pd.read_csv(OUT / "ai_hardware_momentum_refined_20260812.csv")
    breadth = pd.read_csv(OUT / "market_breadth_20260812.csv", parse_dates=["trade_date"])
    spread = pd.read_csv(OUT / "momentum_spread_summary_20260812.csv")
    audit = json.loads((OUT / "ai_hardware_momentum_refined_20260812_audit.json").read_text(encoding="utf-8"))

    focus_names = [
        "香农芯创", "中微公司", "沪电股份", "太辰光", "剑桥科技", "东山精密",
        "源杰科技", "光库科技", "仕佳光子", "生益科技", "景旺电子", "中际旭创", "新易盛",
    ]
    focus = refined[refined["name"].isin(focus_names)].copy()
    focus["index_exposure_short"] = focus["index_flow_exposure"].replace(
        "未由本次公告确认调入", "本次公告未确认"
    )

    momentum_focus = spread[
        spread["period"].eq("2026-08-03_to_08-11")
    ].copy()
    momentum_focus["signal"] = momentum_focus["signal"].map(
        {"mom5": "5日动量", "mom10": "10日动量", "mom20": "20日动量"}
    )

    title = "AI硬件二次筛选：广度强，但动量接管尚未验证"
    sources = [
        {
            "id": "tdx_refined",
            "label": "本地通达信二次筛选结果",
            "path": "output/ai_hardware_momentum_refined_20260812.csv",
            "query": {
                "engine": "python/pandas",
                "language": "python",
                "sql": "SELECT * FROM read_csv_auto('output/ai_hardware_momentum_refined_20260812.csv', header=true)",
                "description": "对上一轮44只AI硬件候选加入均线广度、动量持续性、7月套牢盘距离和指数调入暴露。",
                "tables_used": ["output/ai_hardware_rotation_20260812.csv", "C:/new_tdx/vipdoc/{sh,sz}/lday/*.day"],
                "filters": ["收盘日=2026-08-12", "候选池=44只", "至少130个交易日"],
                "metric_definitions": [
                    "混合分数=原量价分数+站上5/10日线+受控5日涨幅+合理量比+低延伸+指数调入加分-过热/接近7月高位惩罚",
                    "7月修复位置=(8月12日收盘-7月最低)/(7月最高-7月最低)",
                    "指数暴露仅记录中证指数公司2026年5月29日公告中的明确调入，不代表8月12日发生净申购",
                ],
            },
        },
        {
            "id": "tdx_breadth",
            "label": "本地通达信全A均线广度",
            "path": "output/market_breadth_20260812.csv",
            "query": {
                "engine": "python/pandas",
                "language": "python",
                "sql": "SELECT * FROM read_csv_auto('output/market_breadth_20260812.csv', header=true)",
                "description": "逐日统计有完整均线数据的A股站上5/10/20日均线比例。",
                "tables_used": ["C:/new_tdx/vipdoc/{sh,sz}/lday/*.day"],
                "filters": ["2026-08-03至2026-08-12", "每日至少5158只有效股票"],
                "metric_definitions": ["均线广度=收盘价高于指定均线的有效股票数/当日有效股票数"],
            },
        },
        {
            "id": "tdx_momentum",
            "label": "本地通达信动量持续性检验",
            "path": "output/momentum_spread_summary_20260812.csv",
            "query": {
                "engine": "python/pandas",
                "language": "python",
                "sql": "SELECT * FROM read_csv_auto('output/momentum_spread_summary_20260812.csv', header=true)",
                "description": "按过去5/10/20日收益分组，比较最强20%与最弱20%下一交易日的等权收益。",
                "tables_used": ["C:/new_tdx/vipdoc/{sh,sz}/lday/*.day"],
                "filters": ["信号日=2026-07-01至2026-08-11", "每日至少1000只有效股票", "不含交易成本"],
                "metric_definitions": ["动量价差=强势前20%下一日等权收益-弱势后20%下一日等权收益"],
            },
        },
        {
            "id": "csi_notice",
            "label": "中证指数公司2026年5月样本调整公告",
            "href": "https://oss-ch.csindex.com.cn/notice/20260529155822-%E9%99%84%E4%BB%B6%EF%BC%9A%E9%83%A8%E5%88%86%E6%8C%87%E6%95%B0%E6%A0%B7%E6%9C%AC%E8%B0%83%E6%95%B4%E5%90%8D%E5%8D%95.pdf",
        },
    ]

    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": "用本地通达信数据验证截图中的均线、动量、ETF被动资金和套牢盘观点，并重排AI硬件候选。",
        "generatedAt": "2026-08-13T08:00:00+08:00",
        "sources": sources,
        "charts": [
            {
                "id": "breadth_chart",
                "title": "全A站上均线的比例",
                "subtitle": "8月3日至12日，站上20日线比例持续上升，但5日线广度波动较大",
                "type": "line",
                "dataset": "breadth",
                "sourceId": "tdx_breadth",
                "encodings": {
                    "x": {"field": "trade_date", "type": "temporal", "label": "交易日"},
                    "y": {
                        "fields": ["above_ma5_pct", "above_ma10_pct", "above_ma20_pct"],
                        "type": "quantitative",
                        "label": "股票占比",
                        "unit": "%",
                    },
                },
                "layout": "full",
            },
            {
                "id": "momentum_chart",
                "title": "不同周期动量组合的下一日价差",
                "subtitle": "8月3日至11日，强势前20%均落后弱势后20%",
                "type": "bar",
                "dataset": "momentum_focus",
                "sourceId": "tdx_momentum",
                "encodings": {
                    "x": {"field": "signal", "type": "nominal", "label": "排序周期"},
                    "y": {"field": "compounded_spread_pct", "type": "quantitative", "label": "累计价差", "unit": "%"},
                },
                "layout": "full",
            },
            {
                "id": "focus_chart",
                "title": "重点候选混合评分",
                "subtitle": "在原量价分数基础上，奖励受控修复与明确指数调入，惩罚过热和接近套牢区",
                "type": "bar",
                "dataset": "focus",
                "sourceId": "tdx_refined",
                "encodings": {
                    "x": {"field": "name", "type": "nominal", "label": "股票"},
                    "y": {"field": "hybrid_score", "type": "quantitative", "label": "混合评分"},
                    "tooltip": [
                        {"field": "sector", "type": "text"},
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
                "title": "重点候选二次筛选明细",
                "subtitle": "指数调入是被动资金暴露，不是当日净流入证据；触发位仍需盘中量能确认",
                "dataset": "focus",
                "sourceId": "tdx_refined",
                "defaultSort": {"field": "hybrid_score", "direction": "desc"},
                "density": "dense",
                "columns": [
                    {"field": "name", "label": "股票"},
                    {"field": "sector", "label": "板块"},
                    {"field": "hybrid_status", "label": "状态"},
                    {"field": "hybrid_score", "label": "评分", "format": "number"},
                    {"field": "ret5_pct", "label": "5日%", "format": "number", "movement": True},
                    {"field": "vr20_prior", "label": "量比", "format": "number"},
                    {"field": "close_vs_ma20_pct", "label": "距MA20%", "format": "number"},
                    {"field": "july_recovery_pct", "label": "7月修复位%", "format": "number"},
                    {"field": "index_exposure_short", "label": "指数暴露"},
                    {"field": "trigger_price", "label": "触发位", "format": "number"},
                    {"field": "invalidation_price", "label": "失效位", "format": "number"},
                ],
                "layout": "full",
            }
        ],
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {
                "id": "summary",
                "type": "markdown",
                "body": "## Executive Summary\n\n- **截图只验证了一半。** 8月12日全A站上10日线82.4%、站上20日线90.1%，广度修复属实；但8月3日至11日，5/10/20日强势组下一日都跑输弱势组，纯动量接管尚未成立。\n- **选股应改成‘中期结构+受控反弹+盘中量能’，而不是追最热。** 最终一级名单只保留中微公司、沪电股份；香农芯创因中期趋势弱，只适合小仓试错。\n- **ETF逻辑只能用作加分。** 香农、源杰、仕佳、光库等被官方公告明确调入指数，具备被动资金暴露；但没有8月12日逐ETF申赎和成分股成交拆解，不能认定当日净买入。\n- **光通信整体降级。** 太辰光、剑桥、源杰中期趋势仍弱，仕佳、光库又明显过热；全部先观察，不作为下一交易日首发买单。",
            },
            {
                "id": "view_test",
                "type": "markdown",
                "body": "## 截图观点的量化检验\n\n均线广度证明市场从7月急跌中大面积修复，但短周期动量组合没有表现出正向延续。最合理的解释是：市场处于高广度轮动/反转修复，而不是量化资金已经单向切换到追涨模型。因此，站上5日和10日线应当是入池条件，不能单独构成买点。",
            },
            {"id": "breadth_block", "type": "chart", "chartId": "breadth_chart"},
            {"id": "momentum_note", "type": "markdown", "body": "## 强势股没有获得稳定的次日优势\n\n下图的负值表示前期强者下一日落后前期弱者。8月样本仅7个信号日，结论仍属短样本，但5/10/20日三个口径方向一致，足以否定‘已经确认动量接管’这一强断言。"},
            {"id": "momentum_block", "type": "chart", "chartId": "momentum_chart"},
            {"id": "selection_note", "type": "markdown", "body": "## 二次筛选：先看中期结构，再看被动资金放大\n\n最终第一梯队只有中微公司和沪电股份。中微20日跌幅仅2.6%、距MA20约4.4%，中期结构相对完整；沪电60日相对强度高、触发风险约3.9%，但当日量比仅0.54，必须等盘中补量。香农虽然指数暴露和短线修复突出，但20日跌26%、RPS20接近市场底部，只能作为小仓确认单。光通信中，太辰、剑桥、源杰均因20日跌幅超过20%且20日线下行而降级。"},
            {"id": "focus_chart_block", "type": "chart", "chartId": "focus_chart"},
            {"id": "focus_table_block", "type": "table", "tableId": "focus_table"},
            {
                "id": "execution",
                "type": "markdown",
                "body": "## 下一交易日执行顺序\n\n1. **中微公司：第一条件单。** 放量站上386.68且设备板块核心多数在均价线上，仓位3%至5%；结构失效365.63。\n2. **沪电股份：第二条件单。** 10:00后放量站上124.11，PCB核心至少2/3在均价线上，仓位3%至5%；结构失效119.40。\n3. **香农芯创仅作小仓试错。** 站上166.33且存储核心三只以上在均价线上才考虑不超过3%；失效154.82。它不能因指数调入就升级为主仓。\n4. **光通信暂不下首发买单。** 太辰、剑桥、源杰保留观察；仕佳、光库等待缩量回踩10日线或3至5日横盘。\n5. **生益、景旺、胜宏不追。** PCB仍是产业强线，但短线拥挤度高，等待整理。\n\n最多新增一只。任何触发缺少量能或板块共振，都视为无效。",
            },
            {
                "id": "qa",
                "type": "markdown",
                "body": f"## 数据校验与限制\n\n本地通达信读取{audit['stocks_loaded']:,}只、{audit['rows_loaded']:,}行，最新收盘日为8月12日；44只候选无重复、二次字段无缺失。动量检验覆盖7月23个交易日和8月7个信号日，采用全市场等权分组、不含费用。ETF净申购的逐日、逐产品拆解不可得，因此报告只使用官方指数调入资格，不把它写成已经发生的净买入。T+1下隔夜跳空可能使实际止损劣于失效位。",
            },
        ],
    }

    snapshot = {
        "version": 1,
        "generatedAt": "2026-08-13T08:00:00+08:00",
        "status": "ready",
        "datasets": {
            "breadth": records(
                breadth,
                ["trade_date", "stocks", "above_ma5_pct", "above_ma10_pct", "above_ma20_pct"],
            ),
            "momentum_focus": records(
                momentum_focus,
                ["signal", "observations", "avg_daily_spread_pct", "median_daily_spread_pct", "compounded_spread_pct"],
            ),
            "focus": records(
                focus.sort_values("hybrid_score", ascending=False),
                [
                    "name", "sector", "hybrid_status", "hybrid_score", "trade_score", "ret5_pct",
                    "ret10_pct", "ret20_pct", "vr20_prior", "close_vs_ma20_pct", "july_recovery_pct",
                    "gap_to_july_high_pct", "index_exposure_short", "trigger_price", "invalidation_price",
                    "trigger_risk_pct",
                ],
            ),
        },
    }
    artifact = {"surface": "report", "manifest": manifest, "snapshot": snapshot, "sources": sources}
    (OUT / "ai_hardware_momentum_refinement_20260812_artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"artifact": str(OUT / "ai_hardware_momentum_refinement_20260812_artifact.json"), "datasets": {k: len(v) for k, v in snapshot["datasets"].items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
