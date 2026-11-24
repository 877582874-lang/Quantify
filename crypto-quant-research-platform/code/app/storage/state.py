"""SQLite state store: jobs, cursors, runs, raw batches, quality, file catalog. All times UTC ms."""
import sqlite3
import os

SCHEMA = """
CREATE TABLE IF NOT EXISTS collect_jobs(
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  exchange TEXT, market_type TEXT, instrument_id TEXT, interval TEXT,
  kind TEXT, range_start_utc INTEGER, range_end_utc INTEGER,
  status TEXT, created_at_utc INTEGER, updated_at_utc INTEGER);
CREATE TABLE IF NOT EXISTS cursors(
  exchange TEXT, market_type TEXT, instrument_id TEXT, interval TEXT,
  backfill_after_utc INTEGER, last_final_open_time_utc INTEGER, last_success_at_utc INTEGER,
  total_fetched INTEGER DEFAULT 0, total_final INTEGER DEFAULT 0,
  total_duplicates INTEGER DEFAULT 0, request_count INTEGER DEFAULT 0, failure_count INTEGER DEFAULT 0,
  ingest_cursor_utc INTEGER,
  PRIMARY KEY(exchange, market_type, instrument_id, interval));
CREATE TABLE IF NOT EXISTS collect_runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER, started_at_utc INTEGER, finished_at_utc INTEGER,
  fetched INTEGER, inserted INTEGER, duplicates INTEGER, failures INTEGER, error_summary TEXT);
CREATE TABLE IF NOT EXISTS raw_batches(
  batch_id TEXT PRIMARY KEY, path TEXT, sha256 TEXT,
  exchange TEXT, market_type TEXT, instrument_id TEXT, interval TEXT,
  received_at_utc INTEGER, rows_total INTEGER, rows_final INTEGER,
  min_open_time_utc INTEGER, max_open_time_utc INTEGER, request_params TEXT);
CREATE TABLE IF NOT EXISTS quality_checks(
  check_id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT, check_type TEXT, checked_range TEXT, anomaly_count INTEGER,
  detail_json TEXT, created_at_utc INTEGER);
CREATE TABLE IF NOT EXISTS quality_anomalies(
  anomaly_id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT, anomaly_type TEXT, open_time_utc INTEGER, detail TEXT,
  raw_batch_id TEXT, status TEXT DEFAULT 'open', created_at_utc INTEGER);
CREATE TABLE IF NOT EXISTS files_catalog(
  path TEXT PRIMARY KEY, kind TEXT, exchange TEXT, market_type TEXT, instrument_id TEXT,
  interval TEXT, date_utc TEXT, rows INTEGER, min_open_time_utc INTEGER, max_open_time_utc INTEGER,
  sha256 TEXT, archived_at_utc INTEGER);
"""


def ensure_cursor_columns(con):
    """Additive, idempotent migration (B1-R): ingest_cursor_utc tracks the oldest
    open_time actually committed to the normalized dataset. Existing rows keep
    NULL; the backfill derives the resume point from DuckDB in that case."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(cursors)").fetchall()]
    if cols and "ingest_cursor_utc" not in cols:
        con.execute("ALTER TABLE cursors ADD COLUMN ingest_cursor_utc INTEGER")
        con.commit()


def connect(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    con.commit()
    ensure_cursor_columns(con)
    return con
