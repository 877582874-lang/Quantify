#!/usr/bin/env python3
"""B1 Step 4: OKX multi-market incremental collector (controlled run).

Polls OKX public history-candles for 20 series (BTC/ETH x spot/swap x
1m/5m/15m/1h/4h), stores final (confirm=1) bars through the existing
raw -> DuckDB -> Parquet pipeline. Self-terminates at --duration-seconds.
Never rewrites a bar at or below the series' current max open: late or
corrected bars are logged as data anomalies, not applied.

Logs (JSON lines, rotating) under logs/continuous/:
  collector.log api_requests.log ingest.log errors.log quality.log heartbeat.log
Final statistics are written to logs/continuous/summary.json.
"""
import argparse
import json
import logging
import os
import shutil
import signal
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.storage import normalizer, raw_store, state  # noqa: E402

BASE = os.path.join(os.path.dirname(__file__), "..")
STATE_DB = os.path.join(BASE, "data/state.db")
DUCK_DB = os.path.join(BASE, "data/normalized/klines.duckdb")
RAW_BASE = os.path.join(BASE, "data/raw")
HIST_BASE = os.path.join(BASE, "data/historical")
LOG_DIR = os.path.join(BASE, "logs/continuous")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
API_URL = "https://www.okx.com/api/v5/market/history-candles"
STEP = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000}
BAR_API = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}  # OKX wants uppercase H
SERIES = [(mkt, inst) for mkt, inst in
          [("spot", "BTC-USDT"), ("spot", "ETH-USDT"),
           ("swap", "BTC-USDT-SWAP"), ("swap", "ETH-USDT-SWAP")]
          for _ in [0]]
INTERVALS = ["1m", "5m", "15m", "1h", "4h"]
SAFETY_DELAY_MS = 8000
MIN_DISK_FREE_GB = 3.0


def now_ms():
    return int(time.time() * 1000)


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def make_logger(name):
    lg = logging.getLogger(name)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    h = RotatingFileHandler(os.path.join(LOG_DIR, f"{name}.log"),
                            maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(h)
    return lg


LOGS = {}


def jlog(name, **kw):
    rec = {"timestamp_utc": iso(now_ms()), "ts_ms": now_ms()}
    rec.update(kw)
    LOGS[name].info(json.dumps(rec, ensure_ascii=False))


class ApiError(Exception):
    pass


class Feed:
    """OKX public candles with per-attempt logging and bounded retries."""

    def __init__(self):
        self.session = requests.Session()
        self.last_request_at = 0.0
        self.requests = 0
        self.failures = 0
        self.retries = 0
        self.error_kinds = {}

    def candles(self, market_type, instrument_id, interval, job_id, after_ms=None, limit=15):
        params = {"instId": instrument_id, "bar": BAR_API[interval], "limit": str(limit)}
        if after_ms is not None:
            params["after"] = str(after_ms)
        attempt = 0
        while True:
            attempt += 1
            wait = 0.35 - (time.time() - self.last_request_at)
            if wait > 0:
                time.sleep(wait)
            t0 = time.time()
            try:
                r = self.session.get(API_URL, params=params, headers=UA, timeout=20)
                self.last_request_at = time.time()
                latency = int((time.time() - t0) * 1000)
                self.requests += 1
                jlog("api_requests", exchange="okx", market_type=market_type,
                     instrument_id=instrument_id, interval=interval, job_id=job_id,
                     endpoint="/api/v5/market/history-candles",
                     params_summary={"instId": instrument_id, "bar": interval,
                                     "after": after_ms, "limit": limit},
                     http_status=r.status_code, latency_ms=latency, attempt=attempt,
                     status="ok" if r.status_code == 200 else "http_error")
                if r.status_code != 200:
                    raise ApiError(f"HTTP {r.status_code}")
                body = r.json()
                if body.get("code") != "0":
                    err = ApiError(f"OKX code {body.get('code')}: {body.get('msg')}")
                    if str(body.get("code")) in ("51000", "51001", "51008"):
                        err.non_retryable = True  # parameter-class errors: retrying cannot help
                    raise err
                return body["data"]
            except Exception as e:
                self.last_request_at = time.time()
                kind = "api_error" if isinstance(e, (ApiError, requests.RequestException)) else "program_error"
                if attempt >= 4 or getattr(e, "non_retryable", False):
                    self.failures += 1
                    self.error_kinds[kind] = self.error_kinds.get(kind, 0) + 1
                    jlog("api_requests", exchange="okx", market_type=market_type,
                         instrument_id=instrument_id, interval=interval, job_id=job_id,
                         endpoint="/api/v5/market/history-candles", attempt=attempt,
                         status="failed", error_type=kind, error=str(e)[:300])
                    raise
                self.retries += 1
                jlog("api_requests", exchange="okx", market_type=market_type,
                     instrument_id=instrument_id, interval=interval, job_id=job_id,
                     endpoint="/api/v5/market/history-candles", attempt=attempt,
                     status="retrying", error_type=kind, error=str(e)[:300])
                time.sleep(min(2 ** attempt, 16))


class SeriesState:
    def __init__(self, market_type, instrument_id, interval, job_id, anchor_open):
        self.market_type = market_type
        self.instrument_id = instrument_id
        self.interval = interval
        self.step = STEP[interval]
        self.job_id = job_id
        self.anchor_open = anchor_open        # max open when the run started
        self.last_final = anchor_open         # current max final open in DuckDB
        self.fetched = 0
        self.inserted = 0
        self.duplicates = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.backoff_until = 0
        self.corrections_seen = 0
        self.disabled = False
        self.disabled_reason = None

    def key(self):
        return f"{self.market_type}/{self.instrument_id}/{self.interval}"


class Collector:
    def __init__(self, duration_s):
        self.duration_s = duration_s
        self.started_ms = now_ms()
        self.deadline_ms = self.started_ms + duration_s * 1000
        self.stop_flag = False
        self.feed = Feed()
        self.con_state = state.connect(STATE_DB)
        self.con_duck = normalizer.open_db(DUCK_DB)
        self.series = []
        self.quality_anomalies = 0
        self.last_quality_ms = 0
        self.last_heartbeat_ms = 0
        self.last_archive_ms = self.started_ms
        self.disk_alarm = False

    # ---------- setup ----------
    def setup(self):
        for mkt, inst in SERIES:
            for iv in INTERVALS:
                cur = self.con_state.execute(
                    "INSERT INTO collect_jobs(exchange, market_type, instrument_id, interval, kind, "
                    "range_start_utc, range_end_utc, status, created_at_utc, updated_at_utc) "
                    "VALUES ('okx', ?, ?, ?, 'incremental', ?, ?, 'running', ?, ?)",
                    (mkt, inst, iv, self.started_ms, self.deadline_ms, self.started_ms, self.started_ms))
                job_id = cur.lastrowid
                rows_q = self.con_duck.execute(
                    "SELECT max(open_time_utc) FROM klines WHERE exchange='okx' AND market_type=? "
                    "AND instrument_id=? AND interval=?", [mkt, inst, iv]).fetchall()
                anchor = rows_q[0][0] if rows_q else None
                self.series.append(SeriesState(mkt, inst, iv, job_id, anchor))
        self.con_state.commit()
        jlog("collector", event="started", series=len(self.series),
             started_at_utc=iso(self.started_ms), deadline_utc=iso(self.deadline_ms),
             duration_seconds=self.duration_s)

    # ---------- per-series poll ----------
    def poll_series(self, s: SeriesState):
        t = now_ms()
        if s.disabled or t < s.backoff_until:
            return
        if s.last_final is None:
            due = True
        else:
            due = t >= s.last_final + 2 * s.step + SAFETY_DELAY_MS
        if not due:
            return
        try:
            self._poll_series_inner(s)
            s.consecutive_failures = 0
        except Exception as e:
            s.failures += 1
            s.consecutive_failures += 1
            s.backoff_until = now_ms() + min(2 ** s.consecutive_failures * 15000, 300000)
            kind = ("api_error" if isinstance(e, (ApiError, requests.RequestException))
                    else "storage_error" if "duckdb" in type(e).__module__ or "sqlite" in type(e).__module__
                    else "program_error")
            jlog("errors", exchange="okx", market_type=s.market_type, instrument_id=s.instrument_id,
                 interval=s.interval, job_id=s.job_id, status="series_poll_failed",
                 error_type=kind, error=str(e)[:500], traceback=traceback.format_exc()[-1500:],
                 consecutive_failures=s.consecutive_failures)

    def _poll_series_inner(self, s: SeriesState):
        rows = self.feed.candles(s.market_type, s.instrument_id, s.interval, s.job_id, limit=15)
        received = now_ms()
        pages = 1
        # If the newest page still leaves a gap above last_final, page backwards.
        while rows and s.last_final is not None and pages <= 10:
            finals = [int(r[0]) for r in rows if str(r[8]) == "1"]
            if not finals or min(finals) <= s.last_final + s.step:
                break
            older = self.feed.candles(s.market_type, s.instrument_id, s.interval, s.job_id,
                                      after_ms=min(int(r[0]) for r in rows), limit=100)
            if not older:
                break
            rows = rows + older
            pages += 1
        if not rows:
            return
        params = {"instId": s.instrument_id, "bar": s.interval, "limit": 15, "pages": pages}
        manifest = raw_store.save_raw_batch(RAW_BASE, "okx", s.market_type, s.instrument_id,
                                            s.interval, received, params, rows)
        jlog("ingest", exchange="okx", market_type=s.market_type, instrument_id=s.instrument_id,
             interval=s.interval, job_id=s.job_id, batch_id=manifest["batch_id"],
             event="raw_saved", rows_total=manifest["rows_total"],
             rows_final=manifest["rows_final"], sha256=manifest["sha256"], status="ok")
        normalize = normalizer.normalize_okx_swap if s.market_type == "swap" else normalizer.normalize_okx_spot
        # one batched lookup of already-stored closes (fetchone in a loop leaves
        # DuckDB results unfinished and blocks the connection)
        old_ts = [int(r[0]) for r in rows
                  if str(r[8]) == "1" and s.last_final is not None and int(r[0]) <= s.last_final]
        stored_closes = {}
        if old_ts:
            q = ("SELECT open_time_utc, close FROM klines WHERE exchange='okx' AND market_type=? "
                 "AND instrument_id=? AND interval=? AND open_time_utc IN (" +
                 ",".join("?" * len(old_ts)) + ")")
            for ots, oclose in self.con_duck.execute(
                    q, [s.market_type, s.instrument_id, s.interval] + old_ts).fetchall():
                stored_closes[ots] = oclose
        std_rows = []
        for raw in rows:
            ts = int(raw[0])
            if str(raw[8]) != "1":
                continue
            if s.last_final is not None and ts <= s.last_final:
                # already stored: verify it is not a silent correction
                if ts in stored_closes and abs(stored_closes[ts] - float(raw[4])) > 1e-12:
                    s.corrections_seen += 1
                    self.quality_anomalies += 1
                    jlog("quality", exchange="okx", market_type=s.market_type,
                         instrument_id=s.instrument_id, interval=s.interval, job_id=s.job_id,
                         check="late_correction_detected", open_time_utc=ts,
                         stored_close=stored_closes[ts], api_close=float(raw[4]),
                         status="anomaly_not_applied")
                continue
            if ts + s.step > received:  # claims final but close is in the future: distrust
                self.quality_anomalies += 1
                jlog("quality", exchange="okx", market_type=s.market_type,
                     instrument_id=s.instrument_id, interval=s.interval, job_id=s.job_id,
                     check="final_with_future_close", open_time_utc=ts, status="anomaly_skipped")
                continue
            r = normalize(raw, received, manifest["batch_id"], s.instrument_id)
            r["interval"] = s.interval
            r["close_time_utc"] = ts + s.step - 1
            std_rows.append(r)
        s.fetched += len(rows)
        if std_rows:
            ins, dups = normalizer.insert_final(self.con_duck, std_rows)
            s.inserted += ins
            s.duplicates += dups
            s.last_final = max(r["open_time_utc"] for r in std_rows)
            jlog("ingest", exchange="okx", market_type=s.market_type, instrument_id=s.instrument_id,
                 interval=s.interval, job_id=s.job_id, batch_id=manifest["batch_id"],
                 event="duckdb_insert", inserted=ins, duplicates=dups,
                 last_final_open_utc=iso(s.last_final), status="ok")
        self.con_state.execute(
            "INSERT INTO raw_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (manifest["batch_id"], manifest["path"], manifest["sha256"], "okx", s.market_type,
             s.instrument_id, s.interval, received, manifest["rows_total"], manifest["rows_final"],
             manifest["min_open_time_utc"], manifest["max_open_time_utc"], json.dumps(params)))
        self.con_state.execute(
            "INSERT INTO cursors(exchange, market_type, instrument_id, interval, backfill_after_utc, "
            "last_final_open_time_utc, last_success_at_utc, total_fetched, total_final, total_duplicates, "
            "request_count, failure_count, ingest_cursor_utc) VALUES ('okx',?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(exchange,market_type,instrument_id,interval) DO UPDATE SET "
            "last_final_open_time_utc=excluded.last_final_open_time_utc, "
            "last_success_at_utc=excluded.last_success_at_utc, "
            "total_fetched=cursors.total_fetched+excluded.total_fetched, "
            "total_final=cursors.total_final+excluded.total_final, "
            "total_duplicates=cursors.total_duplicates+excluded.total_duplicates, "
            "request_count=excluded.request_count, failure_count=excluded.failure_count",
            (s.market_type, s.instrument_id, s.interval, None, s.last_final, received,
             len(rows), len(std_rows), s.duplicates, self.feed.requests, self.feed.failures,
             None))
        self.con_state.commit()

    # ---------- quality ----------
    def quality_pass(self):
        t = now_ms()
        behind = []
        for s in self.series:
            if s.disabled:
                continue
            try:
                rows_q = self.con_duck.execute(
                    "SELECT max(open_time_utc), count(*) FROM klines WHERE exchange='okx' AND market_type=? "
                    "AND instrument_id=? AND interval=? AND open_time_utc>?",
                    [s.market_type, s.instrument_id, s.interval, s.anchor_open or 0]).fetchall()
                db_max, run_rows = rows_q[0][0], rows_q[0][1]
                latest_closed_open = ((t - SAFETY_DELAY_MS) // s.step) * s.step - s.step
                missing = 0
                if db_max is not None and latest_closed_open > db_max:
                    missing = (latest_closed_open - db_max) // s.step
                elif db_max is None and s.anchor_open is not None and latest_closed_open > s.anchor_open:
                    missing = (latest_closed_open - s.anchor_open) // s.step
                expected_run = 0
                if s.anchor_open is not None and latest_closed_open > s.anchor_open:
                    expected_run = (latest_closed_open - s.anchor_open) // s.step
                gaps = expected_run - run_rows if expected_run else 0
                bad = self.con_duck.execute(
                    "SELECT count(*) FROM klines WHERE exchange='okx' AND market_type=? AND instrument_id=? "
                    "AND interval=? AND open_time_utc>? AND (high < greatest(open, close) OR low > least(open, close) "
                    "OR high < low OR volume_base < 0 OR volume_quote < 0 OR NOT is_final)",
                    [s.market_type, s.instrument_id, s.interval, s.anchor_open or 0]).fetchall()[0][0]
                cur = self.con_state.execute(
                    "SELECT last_final_open_time_utc FROM cursors WHERE market_type=? AND instrument_id=? AND interval=?",
                    (s.market_type, s.instrument_id, s.interval)).fetchone()
                cursor_ok = (cur is None) or (cur[0] is None) or (db_max is None) or (cur[0] == db_max)
                if missing > 0 or gaps > 0 or bad > 0 or not cursor_ok:
                    self.quality_anomalies += 1
                    behind.append(s.key())
                    jlog("quality", exchange="okx", market_type=s.market_type,
                         instrument_id=s.instrument_id, interval=s.interval, job_id=s.job_id,
                         check="freshness_and_integrity", missing_bars=missing, gap_bars=gaps,
                         bad_rows=bad, cursor_consistent=cursor_ok,
                         db_max_open_utc=iso(db_max) if db_max else None,
                         latest_closed_open_utc=iso(latest_closed_open), status="anomaly")
            except Exception as e:
                jlog("errors", exchange="okx", market_type=s.market_type, instrument_id=s.instrument_id,
                     interval=s.interval, job_id=s.job_id, status="quality_pass_failed",
                     error_type="program_error", error=str(e)[:500],
                     traceback=traceback.format_exc()[-1200:])
        jlog("quality", check="pass_complete", series_checked=len(self.series),
             series_behind=behind, status="ok" if not behind else "anomalies_present")

    # ---------- archive ----------
    def archive_all(self, reason):
        def catalog(rows):
            for r in rows:
                self.con_state.execute(
                    "INSERT OR REPLACE INTO files_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["path"], r["kind"], r["exchange"], r["market_type"], r["instrument_id"],
                     r["interval"], r["date_utc"], r["rows"], r["min_open_time_utc"],
                     r["max_open_time_utc"], r["sha256"], now_ms()))
            self.con_state.commit()
        total_files = 0
        for s in self.series:
            try:
                done = normalizer.archive_interval(self.con_duck, HIST_BASE, s.interval, catalog,
                                                   s.instrument_id, s.market_type)
                total_files += len(done)
            except Exception as e:
                jlog("errors", exchange="okx", market_type=s.market_type, instrument_id=s.instrument_id,
                     interval=s.interval, job_id=s.job_id, status="archive_failed",
                     error_type="storage_error", error=str(e)[:500],
                     traceback=traceback.format_exc()[-1200:])
        jlog("ingest", event="archive_pass", reason=reason, files_written=total_files, status="ok")

    # ---------- main loop ----------
    def run(self):
        self.setup()
        while not self.stop_flag and now_ms() < self.deadline_ms:
            free_gb = shutil.disk_usage("/").free / 1e9
            if free_gb < MIN_DISK_FREE_GB:
                self.disk_alarm = True
                jlog("errors", status="disk_guard_stop", error_type="storage_error",
                     free_gb=round(free_gb, 3), error="free disk below 3GB; stopping collection")
                break
            for s in self.series:
                self.poll_series(s)
            t = now_ms()
            if t - self.last_quality_ms >= 60000:
                self.last_quality_ms = t
                self.quality_pass()
            if t - self.last_heartbeat_ms >= 60000:
                self.last_heartbeat_ms = t
                rss = 0
                try:
                    with open("/proc/self/status") as f:
                        for line in f:
                            if line.startswith("VmRSS"):
                                rss = int(line.split()[1]) // 1024
                except Exception:
                    pass
                jlog("heartbeat", uptime_seconds=int((t - self.started_ms) / 1000),
                     series_active=sum(1 for s in self.series if not s.disabled),
                     total_inserted=sum(s.inserted for s in self.series),
                     total_failures=sum(s.failures for s in self.series),
                     api_requests=self.feed.requests, api_failures=self.feed.failures,
                     disk_free_gb=round(shutil.disk_usage("/").free / 1e9, 2), rss_mb=rss,
                     status="alive")
            if t - self.last_archive_ms >= 3600000:
                self.last_archive_ms = t
                self.archive_all("hourly")
            time.sleep(5)
        self.shutdown()

    def shutdown(self):
        jlog("collector", event="stopping", reason="deadline" if now_ms() >= self.deadline_ms else
             ("disk_guard" if self.disk_alarm else "signal"),
             inserted_total=sum(s.inserted for s in self.series))
        try:
            self.archive_all("final")
        except Exception as e:
            jlog("errors", status="final_archive_failed", error_type="storage_error",
                 error=str(e)[:500], traceback=traceback.format_exc()[-1200:])
        try:
            self.quality_pass()
        except Exception:
            pass
        summary = {
            "started_at_utc": iso(self.started_ms), "ended_at_utc": iso(now_ms()),
            "planned_duration_seconds": self.duration_s,
            "actual_duration_seconds": int((now_ms() - self.started_ms) / 1000),
            "disk_alarm": self.disk_alarm,
            "api": {"requests": self.feed.requests, "failures": self.feed.failures,
                    "retries": self.feed.retries, "error_kinds": self.feed.error_kinds},
            "quality_anomaly_events": self.quality_anomalies,
            "series": {s.key(): {"job_id": s.job_id, "anchor_open_utc": iso(s.anchor_open) if s.anchor_open else None,
                                 "last_final_open_utc": iso(s.last_final) if s.last_final else None,
                                 "fetched_rows": s.fetched, "inserted": s.inserted,
                                 "duplicates": s.duplicates, "failures": s.failures,
                                 "corrections_seen": s.corrections_seen,
                                 "disabled": s.disabled}
                       for s in self.series},
        }
        with open(os.path.join(LOG_DIR, "summary.json"), "w") as f:
            json.dump(summary, f, indent=1)
        for s in self.series:
            self.con_state.execute("UPDATE collect_jobs SET status=?, updated_at_utc=? WHERE job_id=?",
                                   ("complete", now_ms(), s.job_id))
        self.con_state.commit()
        jlog("collector", event="stopped", summary_written=True, status="ok")
        self.con_state.close()
        self.con_duck.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-seconds", type=int, default=43200)
    args = ap.parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)
    for name in ["collector", "api_requests", "ingest", "errors", "quality", "heartbeat"]:
        LOGS[name] = make_logger(name)
    c = Collector(args.duration_seconds)

    def on_signal(signum, frame):
        c.stop_flag = True
        jlog("collector", event="signal_received", signal=signum, status="stopping")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    c.run()


if __name__ == "__main__":
    main()
