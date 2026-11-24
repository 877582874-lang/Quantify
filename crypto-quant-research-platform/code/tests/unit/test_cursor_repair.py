"""B1-R cursor-repair regression tests (stdlib unittest, no production data).

Run from project root:
  ./.venv-data/bin/python -m unittest tests.unit.test_cursor_repair -v

All state lives in per-test temp dirs; the API is a scripted fake that emulates
OKX history-candles pagination (rows newest->oldest, ts < after, page size 100).
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.collectors.backfill as backfill_mod
from app.storage import normalizer, state

STEP = 300000  # 5m
BASE = 1792930200000  # aligned 5m open


def make_bars(n, start=BASE, step=STEP, price=100.0, final=True):
    """OKX-style raw rows, ascending time. vol fields differ spot vs swap layout
    but share indices 5/6/7 used by both normalizers."""
    rows = []
    for i in range(n):
        ts = start + i * step
        rows.append([str(ts), str(price), str(price + 1), str(price - 1), str(price),
                     "10", "1.0", "1000.0", "1" if final else "0"])
    return rows


class FakeClient:
    """Emulates OkxClient.history_candles_page over a fixed ascending dataset."""

    def __init__(self, bars, fail_on_call=None):
        self.bars = bars
        self.request_count = 0
        self.failure_count = 0
        self.fail_on_call = fail_on_call  # 1-based call number that raises

    def history_candles_page(self, inst_id, interval, after_ms=None, limit=100):
        self.request_count += 1
        if self.fail_on_call is not None and self.request_count == self.fail_on_call:
            self.failure_count += 1
            raise RuntimeError("simulated process/API crash")
        eligible = [r for r in self.bars if after_ms is None or int(r[0]) < after_ms]
        page = list(reversed(eligible))[:limit]
        return page, {"rows": len(page)}


def new_job(con_state, market="spot", instrument="BTC-USDT"):
    cur = con_state.execute(
        "INSERT INTO collect_jobs(exchange, market_type, instrument_id, interval, kind, "
        "range_start_utc, range_end_utc, status, created_at_utc, updated_at_utc) "
        "VALUES ('okx', ?, ?, '5m', 'backfill', 0, 0, 'running', 0, 0)",
        (market, instrument))
    con_state.commit()
    return cur.lastrowid


class Env:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="b1r-test-")
        self.state_path = os.path.join(self.dir, "state.db")
        self.duck_path = os.path.join(self.dir, "klines.duckdb")
        self.raw_base = os.path.join(self.dir, "raw")
        self.con_state = state.connect(self.state_path)
        self.con_duck = normalizer.open_db(self.duck_path)
        self.job = new_job(self.con_state)

    def run(self, client, start, end, instrument="BTC-USDT", market="spot", job=None):
        return backfill_mod.backfill_interval(
            self.con_state, self.con_duck, self.raw_base, client, "5m", start, end,
            job or self.job, instrument_id=instrument, market_type=market)

    def opens(self, instrument="BTC-USDT", market="spot"):
        return set(r[0] for r in self.con_duck.execute(
            "SELECT open_time_utc FROM klines WHERE market_type=? AND instrument_id=? AND interval='5m'",
            [market, instrument]).fetchall())

    def count(self, instrument="BTC-USDT", market="spot"):
        return len(self.opens(instrument, market))

    def ingest(self, instrument="BTC-USDT", market="spot"):
        row = self.con_state.execute(
            "SELECT ingest_cursor_utc FROM cursors WHERE market_type=? AND instrument_id=? AND interval='5m'",
            (market, instrument)).fetchone()
        return row[0] if row else None

    def close(self):
        self.con_state.close()
        self.con_duck.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class TestCursorRepair(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_A_normal_multipage(self):
        bars = make_bars(250)
        client = FakeClient(bars)
        res = self.env.run(client, BASE, BASE + 250 * STEP)
        self.assertTrue(res["status"].startswith("complete"), res)
        self.assertEqual(self.env.count(), 250)
        self.assertEqual(self.env.opens(), set(BASE + i * STEP for i in range(250)))
        self.assertEqual(self.env.ingest(), BASE)  # oldest committed bar

    def test_B_window_outside_then_widen_no_gap(self):
        bars = make_bars(400)
        # run 1: narrow window over the newest 150 bars only (pages overshoot below it)
        res1 = self.env.run(FakeClient(bars), BASE + 250 * STEP, BASE + 400 * STEP)
        self.assertTrue(res1["status"].startswith("complete"), res1)
        self.assertEqual(self.env.count(), 150)
        # run 2: widen the window to all 400 bars, resume without --fresh
        job2 = new_job(self.env.con_state)
        res2 = self.env.run(FakeClient(bars), BASE, BASE + 400 * STEP, job=job2)
        self.assertTrue(res2["status"].startswith("complete"), res2)
        self.assertEqual(self.env.count(), 400)
        self.assertEqual(self.env.opens(), set(BASE + i * STEP for i in range(400)))
        self.assertEqual(self.env.ingest(), BASE)

    def test_C_raw_saved_insert_fails_cursor_held(self):
        bars = make_bars(250)
        orig = backfill_mod.insert_final
        calls = {"n": 0}

        def flaky(con, rows):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated DuckDB insert failure")
            return orig(con, rows)
        backfill_mod.insert_final = flaky
        try:
            res = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP)
        finally:
            backfill_mod.insert_final = orig
        self.assertTrue(res["status"].startswith("failed"), res)
        # only page 1 (100 bars) committed; ingest cursor == its oldest bar
        self.assertEqual(self.env.count(), 100)
        self.assertEqual(self.env.ingest(), BASE + 150 * STEP)
        # raw batches on disk >= registered batches: the failed page's raw file exists
        raw_files = []
        for root, _, files in os.walk(self.env.raw_base):
            raw_files += [f for f in files if f.endswith(".json.gz")]
        registered = self.env.con_state.execute("SELECT count(*) FROM raw_batches").fetchone()[0]
        self.assertGreaterEqual(len(raw_files), 2)
        self.assertEqual(registered, 1)
        # resume with healthy insert: completes with no gap, no duplicates in table
        job2 = new_job(self.env.con_state)
        res2 = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP, job=job2)
        self.assertTrue(res2["status"].startswith("complete"), res2)
        self.assertEqual(self.env.count(), 250)
        self.assertEqual(self.env.opens(), set(BASE + i * STEP for i in range(250)))

    def test_D_insert_ok_state_commit_fails_no_dup(self):
        import sqlite3 as _sq

        class FlakyConn(_sq.Connection):
            armed = False
            calls = 0
            fail_on = 2

            def commit(self):
                if self.armed:
                    type(self).calls += 1
                    if type(self).calls == type(self).fail_on:
                        raise RuntimeError("simulated SQLite commit failure")
                return super().commit()

        bars = make_bars(250)
        # rebuild the state connection with the flaky factory on the same temp DB
        self.env.con_state.close()
        con = _sq.connect(self.env.state_path, factory=FlakyConn)
        con.execute("PRAGMA journal_mode=WAL")
        self.env.con_state = con
        self.env.job = new_job(con)
        FlakyConn.armed = True
        try:
            res = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP)
        finally:
            FlakyConn.armed = False
        self.assertTrue(res["status"].startswith("failed"), res)
        # resume: page 2 rows were inserted into DuckDB but never registered;
        # re-fetch + PK dedup must land exactly 250 rows, no gap, no dup
        job2 = new_job(self.env.con_state)
        res2 = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP, job=job2)
        self.assertTrue(res2["status"].startswith("complete"), res2)
        self.assertEqual(self.env.count(), 250)
        self.assertEqual(self.env.opens(), set(BASE + i * STEP for i in range(250)))
        self.assertEqual(self.env.ingest(), BASE)

    def test_E_crash_between_pages_resume(self):
        bars = make_bars(250)
        crashing = FakeClient(bars, fail_on_call=3)  # two pages commit, third request dies
        res = self.env.run(crashing, BASE, BASE + 250 * STEP)
        self.assertTrue(res["status"].startswith("failed"), res)
        self.assertEqual(self.env.count(), 200)
        job2 = new_job(self.env.con_state)
        res2 = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP, job=job2)
        self.assertTrue(res2["status"].startswith("complete"), res2)
        self.assertEqual(self.env.count(), 250)  # no --fresh needed
        self.assertEqual(self.env.opens(), set(BASE + i * STEP for i in range(250)))

    def test_F_overlap_refetch_dedup(self):
        bars = make_bars(250)
        res = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP)
        self.assertTrue(res["status"].startswith("complete"), res)
        batches_before = self.env.con_state.execute("SELECT count(*) FROM raw_batches").fetchone()[0]
        # rewind the ingest cursor by 3 bars: resume must overlap-refetch and dedup
        self.env.con_state.execute(
            "UPDATE cursors SET ingest_cursor_utc = ingest_cursor_utc + ? WHERE instrument_id='BTC-USDT'",
            (3 * STEP,))
        self.env.con_state.commit()
        job2 = new_job(self.env.con_state)
        res2 = self.env.run(FakeClient(bars), BASE, BASE + 250 * STEP, job=job2)
        self.assertTrue(res2["status"].startswith("complete"), res2)
        self.assertGreater(res2["duplicates"], 0)  # overlap really happened
        self.assertEqual(self.env.count(), 250)    # ...but no double counting
        batches_after = self.env.con_state.execute("SELECT count(*) FROM raw_batches").fetchone()[0]
        self.assertGreater(batches_after, batches_before)  # refetch traceable in raw

    def test_G_multi_market_isolation(self):
        specs = [("BTC-USDT", "spot", 100.0), ("ETH-USDT", "spot", 200.0), ("BTC-USDT-SWAP", "swap", 300.0)]
        for inst, mkt, price in specs:
            job = new_job(self.env.con_state, mkt, inst)
            res = self.env.run(FakeClient(make_bars(120, price=price)), BASE, BASE + 120 * STEP,
                               instrument=inst, market=mkt, job=job)
            self.assertTrue(res["status"].startswith("complete"), (inst, res))
        for inst, mkt, _ in specs:
            self.assertEqual(self.env.count(inst, mkt), 120, inst)
            self.assertEqual(self.env.ingest(inst, mkt), BASE, inst)
        cursors = self.env.con_state.execute("SELECT count(*) FROM cursors").fetchone()[0]
        self.assertEqual(cursors, 3)
        # archives are partitioned per market/instrument and carry only their own rows
        def catalog(rows):
            import time as _t
            for r in rows:
                self.env.con_state.execute(
                    "INSERT OR REPLACE INTO files_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["path"], r["kind"], r["exchange"], r["market_type"], r["instrument_id"],
                     r["interval"], r["date_utc"], r["rows"], r["min_open_time_utc"],
                     r["max_open_time_utc"], r["sha256"], int(_t.time() * 1000)))
            self.env.con_state.commit()

        for inst, mkt, _ in specs:
            files = normalizer.archive_interval(self.env.con_duck,
                                                os.path.join(self.env.dir, "hist"),
                                                "5m", catalog, inst, mkt)
            self.assertEqual(sum(f["rows"] for f in files), 120, inst)
            self.assertTrue(all(inst in f["path"] and f"market_type={mkt}" in f["path"] for f in files))

    def test_H_unfinal_head_not_counted(self):
        bars = make_bars(150)
        bars[-1][8] = "0"  # newest bar still forming
        res = self.env.run(FakeClient(bars), BASE, BASE + 150 * STEP)
        self.assertTrue(res["status"].startswith("complete"), res)
        self.assertEqual(self.env.count(), 149)
        self.assertNotIn(BASE + 149 * STEP, self.env.opens())


if __name__ == "__main__":
    unittest.main(verbosity=2)
