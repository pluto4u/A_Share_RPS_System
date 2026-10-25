"""Build source-backed strategy dossiers and an AI-searchable SQLite index.

python tools/strategy_archive.py build
python tools/strategy_archive.py search 龙回头
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from project_paths import OUTPUT_ROOT

ARCHIVE = ROOT / "strategy_archive"
INDEX = OUTPUT_ROOT / "catalog" / "strategies.sqlite"


def curated():
    rows = []
    def add(id, name, source, rule, state="implemented_requires_data", notes=""):
        rows.append(dict(id=id, name=name, source=source, rule=rule, status=state, notes=notes,
                         component="signals"))
    add("market-breadth", "通达信市场宽度与内容出图", "workflows/tdx_breadth.py", "上涨/下跌家数；站上MA5/20/60占比；60日宽度轨迹", "locally_executed", "已以真实本地通达信数据运行；查看运行清单与数据日期，不等同实时行情。")
    add("rps-dual", "双周期RPS选股", "rps_system.py", "50日RPS>90，120日RPS>85；沪深300 MA20门控；市值至少30亿；历史至少120日", notes="入口使用akshare网络数据；本地通达信工作流目前仅提供宽度，不宣称已接通RPS输入。")
    add("sentiment-gate", "实时情绪评分与仓位门控", "sentiment_engine.py", "评分映射0/10/30/60/80%，取趋势门控与宽度上限的最小值", notes="与S5仓位模型不同，不能混用评分阈值。")
    add("risk-overlay", "三层风险控制", "risk_engine.py", "日内止损、滚动回撤、连亏减仓/暂停", "implemented_overlay")
    add("daily-s5", "S5每日信号", "daily_signal.py", "情绪分量化到0/25/50/75/100%；调仓间隔至少5天且仓位差至少15%", notes="依赖情绪评分文件；历史绩效文字未在本次重新回测。")
    for id, name, rule in [
        ("s0", "S0持有基准", "始终满仓"),
        ("s1", "S1区间切换", "情绪温度区间跳变，连续确认后切换"),
        ("s2", "S2极恐退出", "默认评分<30连续3天退出，>40重新进入"),
        ("s3", "S3极恐与反转", "极恐退出加低温回升信号"),
        ("s4", "S4情绪均线趋势", "情绪快5日与慢20日均线比较"),
        ("s5", "S5线性节流对比版", "情绪分映射仓位与调仓节流")]:
        add(id, name, "emotion_validation/final_comparison.py", rule, "research_requires_revalidation", "脚本结论文字仍推荐S2，与每日S5入口不一致；不能以旧文字认定策略优劣。")
    add("etf-rotation", "ETF动量与情绪过滤", "emotion_validation/etf_rotation/rotation_strategy.py", "实际默认快5日/慢10日，权重各50%，Top2；情绪收窄候选池", notes="注释中的20/60日不是当前默认；需ETF行情和行业分类。")
    for id, name in [("etf-a", "A纯ETF轮动"), ("etf-b", "B沪深300ETF持有基准"),
                     ("etf-c", "C S5仓位×沪深300ETF"), ("etf-d", "D S5仓位×ETF轮动")]:
        add(id, name, "emotion_validation/etf_rotation/backtest_rotation.py", "四组独立组合对照；详见源文件", "research_requires_revalidation")
    for id, name, source, rule, note in [
        ("dragon", "龙回头双均线缩量回踩", "rps_analyzer/dragon_pullback_finder.py", "15日涨幅≥25%；回撤12–40%；量低于15日天量20%；MA10/20支撑；初始风险≤8%", "统一配置见config/dragon_strategy.py；旧文案绩效需新数据重跑。"),
        ("pocket-v1", "口袋支点v1", "rps_analyzer/pocket_pivot_finder.py", "量价与均线附近支点识别", "保留旧版本用于比较，不与v2同时算两份独立证据。"),
        ("pocket-v2", "口袋支点v2", "rps_analyzer/pocket_pivot_finder_v2.py", "涨价、量超过过去10日下跌量、MA20/50趋势、前日靠近MA10/20", "实际止损固定6%；注释均线止损与实现不同。"),
        ("trend-pullback", "强趋势均线缩量回踩", "rps_analyzer/pocket_pivot_finder_v2.py", "MA10>MA20>MA50；回踩企稳；缩量；风险过滤", "需独立样本外验证。"),
        ("cup-v1", "杯柄形态v1", "rps_analyzer/cup_handle_finder.py", "杯底、杯右与柄部形态", "局部极值使用center=True，存在前视风险；禁止作为已验证实时策略。"),
        ("cup-v2", "杯柄形态v2", "rps_analyzer/cup_handle_finder_v2.py", "放宽杯深10–40%、柄深5–20%的形态条件", "局部极值使用center=True，需逐日前缀验证。")]:
        add(id, name, source, rule, "research_requires_revalidation" if id.startswith("cup") else "implemented_requires_data", note)
    for letter, name, rule in [("a", "放量突破前高", "收盘破前10日高、阳线、量≥5日均量1.5倍、涨幅≥3%"),
                                ("b", "一进二", "连续两日涨停"),
                                ("c", "横盘突破涨停", "10日波动≤12%、放量涨停"),
                                ("d", "强势股回调确认", "10日≥2次涨停、回撤≤10%、阳线、缩量")]:
        add("douyin-" + letter, "短线" + letter.upper() + " " + name,
            "emotion_validation/strategy_layer/douyin_system/stock_screener.py", rule,
            "research_requires_revalidation", "A标题旧称长上影，实际为突破前高；涨停识别ST/北交所口径不完整，需修正再验收。")
    add("dragon-search", "龙回头V0–V7及深度参数搜索", "run_dragon_optimization.py", "参数搜索实验，不是额外的线上策略", "research_optimizer", "深度搜索入口run_deep_dragon_optimization.py；存在样本内过拟合风险。")
    return rows


def metadata(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("META", "STOP_LOSS", "MAX_HOLD_DAYS", "ENTRY_SIGNALS", "EXIT_SIGNALS"):
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    result["docstring"] = ast.get_docstring(tree) or ""
    return result


def build():
    rows = curated()
    config_path = ROOT / "config/workspace.local.json"
    components = json.loads(config_path.read_text(encoding="utf-8"))["components"] if config_path.exists() else {}
    panel = Path(components.get("panel", {}).get("path", ROOT / "missing-panel"))
    for source in sorted((panel / "backend/app/strategy/builtin").glob("*.py")):
        if source.name == "__init__.py":
            continue
        meta = metadata(source)
        identity = meta.get("META", {})
        rows.append(dict(id="panel-" + source.stem, name=identity.get("name", source.stem),
                         source=str(source), rule=identity.get("description", meta["docstring"]),
                         status="source_present_not_runtime_verified", component="panel",
                         notes="读取源文件META归档；本次未启动面板或验收其运行结果。", parameters=meta))
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    ARCHIVE.mkdir(exist_ok=True)
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(INDEX)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS strategies USING fts5(id UNINDEXED, name, body, tokenize='trigram')")
    db.execute("DELETE FROM strategies")
    for row in rows:
        source = Path(row["source"])
        source = source if source.is_absolute() else ROOT / source
        content = source.read_text(encoding="utf-8-sig")
        digest = hashlib.sha256(content.encode()).hexdigest()
        dest = ARCHIVE / "sources" / f"{digest}.py"
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            dest.write_text(content, encoding="utf-8")
        row.update(schema_version="strategy-dossier/v1", archived_at=now,
                   source_sha256=digest, snapshot=str(dest.relative_to(ARCHIVE)),
                   performance_verified=False,
                   evidence="代码读取与主工程单元测试；非历史收益复验")
        body = f"# {row['name']}\n\n- ID：{row['id']}\n- 组件：{row['component']}\n- 状态：{row['status']}\n- 规则：{row['rule']}\n- 限制：{row['notes']}\n- 来源：{row['source']}\n- 源码SHA256：{digest}\n\n本次不把代码存在、单测通过或旧收益文字视为实盘有效性证明。\n"
        card = ARCHIVE / "cards" / (row["id"] + ".md")
        card.parent.mkdir(exist_ok=True)
        card.write_text(body, encoding="utf-8")
        db.execute("INSERT INTO strategies VALUES (?, ?, ?)", (row["id"], row["name"], body))
    db.commit()
    db.close()
    (ARCHIVE / "registry.json").write_text(json.dumps({"schema_version": "strategy-registry/v1", "generated_at": now, "strategies": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    index = "# 策略档案索引\n\n状态以代码可用性为准，不代表收益认可。\n\n" + "\n".join(
        f"- [{r['name']}](cards/{r['id']}.md) — `{r['status']}`" for r in rows)
    (ARCHIVE / "INDEX.md").write_text(index + "\n", encoding="utf-8")
    print(f"Archived {len(rows)} dossiers: {ARCHIVE}")
    print(f"AI search index: {INDEX}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "search"])
    parser.add_argument("query", nargs="?")
    args = parser.parse_args()
    if args.command == "build":
        build()
    else:
        if not args.query:
            parser.error("search requires query")
        with sqlite3.connect(INDEX) as db:
            # LIKE also handles Chinese queries shorter than the FTS trigram token length.
            for row in db.execute("SELECT id, name FROM strategies WHERE name LIKE ? OR body LIKE ? LIMIT 30",
                                  ("%" + args.query + "%", "%" + args.query + "%")):
                print(f"{row[0]}  {row[1]}")


if __name__ == "__main__":
    main()
