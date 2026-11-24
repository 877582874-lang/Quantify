"""Backfill driver: OKX spot/swap, paginated history-candles, resumable.

Cursor semantics (B1-R repair):
- backfill_after_utc = FETCH progress only (oldest open_time the API has returned
  so far). It is diagnostic and never decides where a resume starts.
- ingest_cursor_utc = oldest open_time that has been successfully normalized AND
  committed to the DuckDB dataset for this series. It advances only after the
  page's DuckDB insert succeeded and the page's SQLite registration commits in
  the same step. Window-filtered or unfinal rows never advance it.
- Resume starts at after = ingest_cursor_utc + step, so the boundary bar is
  re-fetched (overlap) and absorbed by the 5-dim primary-key dedup. If
  ingest_cursor_utc is NULL (legacy rows), the resume point is derived from
  DuckDB min(open_time_utc) for the series, with the same +step overlap.
Per-page commit order: fetch -> save raw (immutable file) -> normalize ->
DuckDB insert -> single SQLite transaction (raw_batches row + cursor update).
Any failure before the SQLite commit leaves the ingest cursor untouched, so a
later run re-fetches the page; duplicates are absorbed by the primary key.
"""
import json
import time

from app.exchanges.okx import OkxClient
from app.storage import raw_store
from app.storage.normalizer import (INTERVAL_MS, insert_final, normalize_okx_spot,
                                    normalize_okx_swap)

def now_ms():
    return int(time.time() * 1000)


def _series_min_open(con_duck, market_type, instrument_id, interval):
    row = con_duck.execute(
        "SELECT min(open_time_utc) FROM klines WHERE exchange='okx' AND market_type=? "
        "AND instrument_id=? AND interval=?",
        [market_type, instrument_id, interval]).fetchone()
    return row[0] if row else None


def backfill_interval(con_state, con_duck, raw_base, client, interval, start_ms, end_ms, job_id,
                      instrument_id="BTC-USDT", market_type="spot"):
    INST = instrument_id
    normalize = normalize_okx_swap if market_type == "swap" else normalize_okx_spot
    step = INTERVAL_MS[interval]
    cur = con_state.execute(
        "SELECT backfill_after_utc, total_fetched, total_final, total_duplicates, ingest_cursor_utc "
        "FROM cursors WHERE exchange='okx' AND market_type=? AND instrument_id=? AND interval=?",
        (market_type, INST, interval)).fetchone()
    fetched = cur[1] if cur else 0
    final_total = cur[2] if cur else 0
    dup_total = cur[3] if cur else 0
    ingest = cur[4] if cur else None
    if ingest is None and cur is not None:
        # legacy cursor without ingest tracking: derive from committed data
        ingest = _series_min_open(con_duck, market_type, INST, interval)
    if ingest is not None:
        after = ingest + step  # overlap: re-fetch the boundary bar, dedup absorbs it
    else:
        after = None  # nothing committed yet: start from the latest page
    run_started = now_ms()
    run_fetched = run_inserted = run_dups = 0
    failures_before = client.failure_count
    status = "complete"
    if after is not None and after <= start_ms:
        status = "complete (already covered)"
    else:
        try:
            while True:
                rows, meta = client.history_candles_page(INST, interval, after_ms=after, limit=100)
                received = now_ms()
                if not rows:
                    break
                params = {"instId": INST, "bar": interval, "after": after, "limit": 100}
                manifest = raw_store.save_raw_batch(raw_base, "okx", market_type, INST, interval,
                                                    received, params, rows)
                std_rows = []
                page_final = 0
                for raw in rows:
                    ts = int(raw[0])
                    if ts < start_ms or ts >= end_ms:
                        continue
                    r = normalize(raw, received, manifest["batch_id"], INST)
                    r["interval"] = interval
                    r["close_time_utc"] = ts + step - 1
                    fetched += 1
                    run_fetched += 1
                    if r["is_final"]:
                        page_final += 1
                        std_rows.append(r)
                # DuckDB insert happens BEFORE any cursor movement.
                ins, dups = insert_final(con_duck, std_rows)
                run_inserted += ins
                run_dups += dups
                final_total += page_final
                dup_total += dups
                if std_rows:
                    page_ingest = min(r["open_time_utc"] for r in std_rows)
                    ingest = page_ingest if ingest is None else min(ingest, page_ingest)
                oldest_fetched = min(int(r[0]) for r in rows)
                # Single SQLite transaction: batch registration + cursor update.
                con_state.execute(
                    "INSERT INTO raw_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (manifest["batch_id"], manifest["path"], manifest["sha256"], "okx", market_type,
                     INST, interval, received, manifest["rows_total"], manifest["rows_final"],
                     manifest["min_open_time_utc"], manifest["max_open_time_utc"], json.dumps(params)))
                con_state.execute(
                    "INSERT INTO cursors(exchange, market_type, instrument_id, interval, backfill_after_utc, "
                    "last_final_open_time_utc, last_success_at_utc, total_fetched, total_final, total_duplicates, "
                    "request_count, failure_count, ingest_cursor_utc) VALUES ('okx',?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(exchange,market_type,instrument_id,interval) DO UPDATE SET "
                    "backfill_after_utc=excluded.backfill_after_utc, last_final_open_time_utc=excluded.last_final_open_time_utc, "
                    "last_success_at_utc=excluded.last_success_at_utc, total_fetched=excluded.total_fetched, "
                    "total_final=excluded.total_final, total_duplicates=excluded.total_duplicates, "
                    "request_count=excluded.request_count, failure_count=excluded.failure_count, "
                    "ingest_cursor_utc=excluded.ingest_cursor_utc",
                    (market_type, INST, interval, oldest_fetched,
                     max([int(r[0]) for r in rows if str(r[8]) == '1'], default=None),
                     received, fetched, final_total, dup_total, client.request_count,
                     client.failure_count, ingest))
                con_state.commit()
                after = oldest_fetched  # continue strictly older within this run
                if oldest_fetched <= start_ms:
                    break
        except Exception as e:
            try:
                con_state.rollback()
            except Exception:
                pass
            status = f"failed: {e}"
    con_state.execute(
        "INSERT INTO collect_runs(job_id, started_at_utc, finished_at_utc, fetched, inserted, duplicates, failures, error_summary) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (job_id, run_started, now_ms(), run_fetched, run_inserted, run_dups,
         client.failure_count - failures_before, None if status.startswith("complete") else status))
    con_state.commit()
    return {"interval": interval, "status": status, "fetched_in_window": run_fetched,
            "inserted_rows": run_inserted, "duplicates": run_dups,
            "ingest_cursor_utc": ingest,
            "requests_total": client.request_count, "failures_total": client.failure_count}
