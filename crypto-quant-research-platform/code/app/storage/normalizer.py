"""Normalization (OKX spot per B1_FIELD_MAPPING) + DuckDB storage + Parquet archive."""
import json
import os
from datetime import datetime, timezone

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

COLUMNS = ["exchange", "market_type", "instrument_id", "native_symbol", "data_type", "interval",
           "open_time_utc", "close_time_utc", "received_at_utc", "is_final",
           "open", "high", "low", "close",
           "volume_native", "volume_base", "volume_quote", "volume_contract",
           "source", "raw_batch_id", "raw_row"]

DDL = """
CREATE TABLE IF NOT EXISTS klines(
  exchange VARCHAR, market_type VARCHAR, instrument_id VARCHAR, native_symbol VARCHAR,
  data_type VARCHAR, interval VARCHAR,
  open_time_utc BIGINT, close_time_utc BIGINT, received_at_utc BIGINT, is_final BOOLEAN,
  open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
  volume_native DOUBLE, volume_base DOUBLE, volume_quote DOUBLE, volume_contract DOUBLE,
  source VARCHAR, raw_batch_id VARCHAR, raw_row VARCHAR,
  PRIMARY KEY(exchange, market_type, instrument_id, interval, open_time_utc))
"""


def normalize_okx_spot(raw, received_ms, batch_id, instrument_id="BTC-USDT"):
    """One OKX spot candle array -> standard dict. Spot mapping: vol=[5] is base qty,
    volCcyQuote=[7] is quote qty, volume_native keeps the raw [5] value untouched."""
    ts = int(raw[0])
    return {
        "exchange": "okx", "market_type": "spot",
        "instrument_id": instrument_id, "native_symbol": instrument_id,
        "data_type": "kline", "interval": None,  # filled by caller
        "open_time_utc": ts, "close_time_utc": None,  # filled by caller (needs interval)
        "received_at_utc": received_ms,
        "is_final": str(raw[8]) == "1",
        "open": float(raw[1]), "high": float(raw[2]), "low": float(raw[3]), "close": float(raw[4]),
        "volume_native": float(raw[5]),
        "volume_base": float(raw[5]),
        "volume_quote": float(raw[7]),
        "volume_contract": None,
        "source": "rest:market/history-candles",
        "raw_batch_id": batch_id,
        "raw_row": json.dumps(raw, separators=(",", ":")),
    }


def normalize_okx_swap(raw, received_ms, batch_id, instrument_id="BTC-USDT-SWAP"):
    """One OKX swap candle array -> standard dict. Swap mapping (verified in
    docs/B1_STEP3B1_CONTRACT_SPEC.md): [5] vol = contracts, [6] volCcy = base qty,
    [7] volCcyQuote = quote qty. volume_base takes the exchange-provided volCcy,
    NOT a ctVal conversion (conversion is used only as a cross-check)."""
    ts = int(raw[0])
    return {
        "exchange": "okx", "market_type": "swap",
        "instrument_id": instrument_id, "native_symbol": instrument_id,
        "data_type": "kline", "interval": None,
        "open_time_utc": ts, "close_time_utc": None,
        "received_at_utc": received_ms,
        "is_final": str(raw[8]) == "1",
        "open": float(raw[1]), "high": float(raw[2]), "low": float(raw[3]), "close": float(raw[4]),
        "volume_native": float(raw[5]),
        "volume_base": float(raw[6]),
        "volume_quote": float(raw[7]),
        "volume_contract": float(raw[5]),
        "source": "rest:market/history-candles",
        "raw_batch_id": batch_id,
        "raw_row": json.dumps(raw, separators=(",", ":")),
    }


def open_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = duckdb.connect(path)
    con.execute("SET memory_limit='512MB'")
    con.execute(DDL)
    return con


def insert_final(con, rows):
    """Insert final rows only; returns (inserted_or_replaced, duplicates_within_batch)."""
    if not rows:
        return 0, 0
    seen = set()
    uniq = []
    dups = 0
    for r in rows:
        key = r["open_time_utc"]
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        uniq.append(r)
    table = pa.table({c: [r[c] for r in uniq] for c in COLUMNS})
    con.register("staging", table)
    before = con.execute("SELECT count(*) FROM klines WHERE interval=? AND instrument_id=? AND open_time_utc IN "
                         "(SELECT open_time_utc FROM staging)",
                         [uniq[0]["interval"], uniq[0]["instrument_id"]]).fetchone()[0]
    con.execute("INSERT OR REPLACE INTO klines SELECT * FROM staging")
    con.unregister("staging")
    return len(uniq), dups + before  # 'before' rows were replaced duplicates from earlier pages/runs


def archive_interval(con, hist_base, interval, catalog_rows, instrument_id="BTC-USDT",
                     market_type="spot"):
    """Archive all dates of one interval+instrument+market from DuckDB to Parquet with the
    5-step protocol. catalog_rows: callback(list_of_dicts) persisting into SQLite files_catalog."""
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT strftime(to_timestamp(open_time_utc/1000), '%Y-%m-%d') FROM klines "
        "WHERE interval=? AND instrument_id=? AND market_type=? ORDER BY 1",
        [interval, instrument_id, market_type]).fetchall()]
    done = []
    for date_str in dates:
        rows = con.execute(
            f"SELECT {', '.join(COLUMNS)} FROM klines WHERE interval=? AND instrument_id=? AND market_type=? AND "
            "strftime(to_timestamp(open_time_utc/1000), '%Y-%m-%d')=? ORDER BY open_time_utc",
            [interval, instrument_id, market_type, date_str]).fetchall()
        table = pa.table({c: [r[i] for r in rows] for i, c in enumerate(COLUMNS)})
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        out_dir = os.path.join(hist_base, "exchange=okx", f"market_type={market_type}",
                               f"instrument_id={instrument_id}", f"interval={interval}",
                               f"year={dt:%Y}", f"month={dt:%m}")
        os.makedirs(out_dir, exist_ok=True)
        final_path = os.path.join(out_dir, f"klines-{date_str.replace('-', '')}.parquet")
        tmp_path = final_path + ".tmp.parquet"
        pq.write_table(table, tmp_path)
        # verify temp file independently through DuckDB read_parquet
        cnt, mn, mx = con.execute(
            "SELECT count(*), min(open_time_utc), max(open_time_utc) FROM read_parquet(?)",
            [tmp_path]).fetchone()
        exp_mn = min(r[6] for r in rows)
        exp_mx = max(r[6] for r in rows)
        if cnt != len(rows) or mn != exp_mn or mx != exp_mx:
            raise RuntimeError(f"archive verify failed {interval} {date_str}: {cnt}/{len(rows)} {mn}/{exp_mn} {mx}/{exp_mx}")
        os.replace(tmp_path, final_path)  # atomic rename
        import hashlib
        h = hashlib.sha256()
        with open(final_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        done.append({"path": final_path, "kind": "parquet", "exchange": "okx", "market_type": market_type,
                     "instrument_id": instrument_id, "interval": interval, "date_utc": date_str,
                     "rows": len(rows), "min_open_time_utc": exp_mn, "max_open_time_utc": exp_mx,
                     "sha256": h.hexdigest()})
    if done:
        catalog_rows(done)
    return done
