"""B1 step 2 orchestrator: OKX BTC-USDT spot backfill -> archive -> quality checks.
Usage: python scripts/run_step2.py --days 30 [--intervals 1m,5m] [--skip-archive]"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.collectors.backfill import backfill_interval, now_ms
from app.data_quality.checks import run_checks
from app.exchanges.okx import OkxClient
from app.storage import state
from app.storage.normalizer import archive_interval, open_db

BASE = "/opt/okx-quant"
RAW = os.path.join(BASE, "data", "raw")
DUCK = os.path.join(BASE, "data", "normalized", "klines.duckdb")
HIST = os.path.join(BASE, "data", "historical")
STATE = os.path.join(BASE, "data", "state.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--instrument", default="BTC-USDT")
    ap.add_argument("--market-type", default="spot")
    ap.add_argument("--start-ms", type=int, default=None)
    ap.add_argument("--end-ms", type=int, default=None)
    ap.add_argument("--intervals", default="1m,5m,15m,1h,4h")
    ap.add_argument("--skip-archive", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="reset backfill cursors first (full re-fetch; PK dedup still applies)")
    args = ap.parse_args()
    intervals = args.intervals.split(",")
    instrument = args.instrument
    market_type = args.market_type
    end_ms = args.end_ms if args.end_ms is not None else now_ms()
    start_ms = args.start_ms if args.start_ms is not None else end_ms - args.days * 86_400_000
    con_state = state.connect(STATE)
    if args.fresh:
        for iv in intervals:
            con_state.execute(
                "DELETE FROM cursors WHERE exchange='okx' AND market_type=? "
                "AND instrument_id=? AND interval=?", (market_type, instrument, iv))
        con_state.commit()
    con_duck = open_db(DUCK)
    client = OkxClient()
    results = []
    for iv in intervals:
        cur = con_state.execute(
            "INSERT INTO collect_jobs(exchange, market_type, instrument_id, interval, kind, range_start_utc, range_end_utc, status, created_at_utc, updated_at_utc) "
            "VALUES ('okx',?,?,?,'backfill',?,?, 'running',?,?) RETURNING job_id",
            (market_type, instrument, iv, start_ms, end_ms, end_ms, end_ms)).fetchone()
        job_id = cur[0]
        con_state.commit()
        res = backfill_interval(con_state, con_duck, RAW, client, iv, start_ms, end_ms, job_id, instrument, market_type)
        con_state.execute("UPDATE collect_jobs SET status=?, updated_at_utc=? WHERE job_id=?",
                          (res["status"], now_ms(), job_id))
        con_state.commit()
        results.append(res)
        print(json.dumps(res), flush=True)
    archive_summary = {}
    if not args.skip_archive:
        def catalog(rows):
            for r in rows:
                con_state.execute(
                    "INSERT OR REPLACE INTO files_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["path"], r["kind"], r["exchange"], r["market_type"], r["instrument_id"],
                     r["interval"], r["date_utc"], r["rows"], r["min_open_time_utc"],
                     r["max_open_time_utc"], r["sha256"], now_ms()))
            con_state.commit()
        for iv in intervals:
            done = archive_interval(con_duck, HIST, iv, catalog, instrument, market_type)
            archive_summary[iv] = {"files": len(done), "rows": sum(d["rows"] for d in done)}
        print(json.dumps({"archive": archive_summary}), flush=True)
    checks = {}
    for iv in intervals:
        checks[iv] = run_checks(con_duck, con_state, iv, start_ms, end_ms, instrument, market_type)
        print(json.dumps({"quality": checks[iv]}), flush=True)
    out = {"window": {"start_ms": start_ms, "end_ms": end_ms, "days": args.days},
           "backfill": results, "archive": archive_summary, "quality": checks,
           "api_requests_total": client.request_count, "api_failures_total": client.failure_count}
    print("FINAL_JSON_BEGIN")
    print(json.dumps(out, indent=1))
    print("FINAL_JSON_END")


if __name__ == "__main__":
    main()
