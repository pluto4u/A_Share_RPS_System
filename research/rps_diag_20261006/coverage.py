"""Finite official-list coverage check. Never mutates the market database."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from collect_local import OUT as LOCAL, query

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/rps_diag_20261006/exchange_evidence"


def fetch(name, url, params, referer, suffix):
    path = OUT / f"{name}.{suffix}"
    if path.exists():
        raise FileExistsError(path)
    receipt = dict(invoked_at_utc=datetime.now(timezone.utc).isoformat(), url=url,
                   params=params, referer=referer, purpose="historical coverage only")
    try:
        response = requests.get(url, params=params, headers={"Referer": referer,
            "User-Agent": "Mozilla/5.0"}, timeout=(10, 35))
        receipt.update(status=response.status_code, content_type=response.headers.get("Content-Type"),
                       bytes=len(response.content), sha256=hashlib.sha256(response.content).hexdigest())
        path.write_bytes(response.content)
        response.raise_for_status()
    except requests.RequestException as exc:
        receipt["error"] = str(exc)
    (OUT / f"{name}_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sql = """WITH dates AS (
      SELECT thscode,MIN(date) AS first_date,MAX(date) AS last_date,COUNT(*) AS observations
      FROM raw_kline_daily GROUP BY thscode)
    SELECT COALESCE(s.asset_type,'NO_DIM') AS asset_type,COALESCE(s.exchange,'NO_DIM') AS exchange,
      COUNT(*) AS securities,SUM(d.observations) AS observations,MIN(d.first_date) AS earliest,
      MAX(d.last_date) AS latest,
      SUM(CASE WHEN d.last_date<DATE '2025-09-30' THEN 1 ELSE 0 END) AS ended_before_20250930
    FROM dates d LEFT JOIN dim_symbol s ON d.thscode=s.thscode
    GROUP BY 1,2 ORDER BY 1,2 LIMIT 30"""
    evidence = query("raw_coverage", sql)
    print(json.dumps(evidence, ensure_ascii=False), flush=True)
    sse = fetch("sse_page1", "https://query.sse.com.cn/commonQuery.do", dict(
        sqlId="COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L", isPagination="true", STOCK_CODE="", CSRC_CODE="",
        REG_PROVINCE="", STOCK_TYPE="1,2,8", COMPANY_STATUS="3", type="inParams",
        **{"pageHelp.cacheSize":"1", "pageHelp.beginPage":"1", "pageHelp.pageSize":"500",
           "pageHelp.pageNo":"1", "pageHelp.endPage":"1"}), "https://www.sse.com.cn/", "json")
    print(json.dumps(sse, ensure_ascii=False), flush=True)
    szse = fetch("szse_terminated", "https://www.szse.cn/api/report/ShowReport",
        dict(SHOWTYPE="xlsx", CATALOGID="1793_ssgs", TABKEY="tab2"), "https://www.szse.cn/", "xlsx")
    print(json.dumps(szse, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
