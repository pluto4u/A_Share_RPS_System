"""Command-line entry for the industry-chain database and RPS bridge."""

from __future__ import annotations

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import argparse
from pathlib import Path

import pandas as pd

from industry_chain.database import DEFAULT_DB_PATH, IndustryChainDB, initialize_database
from industry_chain.rps_bridge import POOL_SUFFIXES, analyze_rps_candidates


ROOT = PROJECT_ROOT


def _ensure_db(path: Path) -> IndustryChainDB:
    if not path.exists():
        initialize_database(path)
    return IndustryChainDB(path)


def _print_frame(rows: list[dict] | pd.DataFrame, columns: list[str] | None = None) -> None:
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if columns:
        columns = [column for column in columns if column in frame.columns]
        frame = frame[columns]
    if frame.empty:
        print("无记录")
    else:
        print(frame.to_string(index=False))


def cmd_init(args: argparse.Namespace) -> None:
    path = initialize_database(args.db, args.seed, reset=args.reset)
    db = IndustryChainDB(path)
    themes = db.list_themes()
    print(f"产业链数据库已就绪: {path}")
    print(f"主题数: {len(themes)}")


def cmd_themes(args: argparse.Namespace) -> None:
    db = _ensure_db(args.db)
    _print_frame(db.list_themes(), ["code", "name", "status", "as_of_date", "demand_driver"])


def cmd_map(args: argparse.Namespace) -> None:
    db = _ensure_db(args.db)
    graph = db.theme_map(args.theme)
    print(f"\n[{args.theme}] 产业环节")
    _print_frame(
        graph["nodes"],
        ["layer_order", "code", "name", "node_type", "bottleneck_type", "as_of_date"],
    )
    print("\n上下游关系")
    _print_frame(
        graph["relations"],
        ["upstream_code", "downstream_code", "relation_type", "criticality", "lead_time_months"],
    )
    print("\n公司映射")
    _print_frame(
        graph["companies"],
        ["security_id", "ts_code", "name", "node_code", "role", "mapping_confidence"],
    )


def cmd_rank(args: argparse.Namespace) -> None:
    db = _ensure_db(args.db)
    rows = db.ranked_exposures(args.theme, min_confidence=args.min_confidence)[: args.limit]
    _print_frame(
        rows,
        [
            "security_id",
            "name",
            "theme_code",
            "node_name",
            "node_score",
            "ownership_score",
            "bottleneck_score",
            "research_confidence",
            "as_of_date",
        ],
    )


def cmd_queue(args: argparse.Namespace) -> None:
    db = _ensure_db(args.db)
    rows = db.research_queue(args.status)
    _print_frame(
        rows,
        [
            "security_code",
            "first_seen_date",
            "last_seen_date",
            "occurrence_count",
            "latest_pool",
            "latest_rps_score",
            "status",
            "notes",
        ],
    )


def cmd_views(args: argparse.Namespace) -> None:
    db = _ensure_db(args.db)
    rows = db.viewpoints_for(args.entity_type, args.entity_code)
    _print_frame(
        rows,
        [
            "as_of_date",
            "source_name",
            "stance",
            "horizon",
            "claim_type",
            "confidence",
            "adoption_status",
            "viewpoint",
            "verification_conditions",
            "disconfirming_conditions",
        ],
    )


def cmd_rps(args: argparse.Namespace) -> None:
    db = _ensure_db(args.db)
    result, meta = analyze_rps_candidates(
        db,
        output_dir=args.output_dir,
        date=args.date,
        pool=args.pool,
    )
    print(
        f"日期={meta['date']}  候选池={meta['pool']}  "
        f"总数={meta['candidate_count']}  已映射={meta['mapped_count']}  "
        f"待建档={meta['unmapped_count']}"
    )
    columns = [
        "ts_code",
        "name",
        "theme_code",
        "node_name",
        "bottleneck_score",
        "research_confidence",
        "rps_score",
        "heat_penalty",
        "combined_score",
        "decision",
    ]
    _print_frame(result, columns)
    if args.write and not result.empty:
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(target, index=False, encoding="utf-8-sig")
        print(f"已写出: {target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="产业链瓶颈数据库与 RPS 联动分析")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="初始化或更新数据库")
    init.add_argument("--seed", type=Path, default=ROOT / "industry_chain" / "seed_data.json")
    init.add_argument("--reset", action="store_true", help="重建数据库")
    init.set_defaults(func=cmd_init)

    themes = subparsers.add_parser("themes", help="列出产业主题")
    themes.set_defaults(func=cmd_themes)

    graph = subparsers.add_parser("map", help="查看产业主题图谱")
    graph.add_argument("--theme", required=True)
    graph.set_defaults(func=cmd_map)

    rank = subparsers.add_parser("rank", help="按瓶颈所有权排名")
    rank.add_argument("--theme")
    rank.add_argument("--min-confidence", type=float, default=0)
    rank.add_argument("--limit", type=int, default=30)
    rank.set_defaults(func=cmd_rank)

    queue = subparsers.add_parser("queue", help="查看待补充产业映射的候选股")
    queue.add_argument("--status", default="pending_mapping")
    queue.set_defaults(func=cmd_queue)

    views = subparsers.add_parser("views", help="查看公司、节点或主题的观点与采纳状态")
    views.add_argument("--entity-type", choices=["security", "node", "theme"], required=True)
    views.add_argument("--entity-code", required=True)
    views.set_defaults(func=cmd_views)

    rps = subparsers.add_parser("rps", help="与现有 RPS 候选池联动")
    rps.add_argument("--date", help="YYYYMMDD，省略时取候选池最新日期")
    rps.add_argument("--pool", choices=sorted(POOL_SUFFIXES), default="super")
    rps.add_argument("--output-dir", type=Path, default=ROOT / "output")
    rps.add_argument("--write", help="可选：写出完整分析 CSV")
    rps.set_defaults(func=cmd_rps)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
