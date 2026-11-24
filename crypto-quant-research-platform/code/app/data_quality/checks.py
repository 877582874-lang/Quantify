"""Quality checks over normalized DuckDB data. Anomalies are recorded, never silently dropped."""
import json
import time

from app.storage.normalizer import INTERVAL_MS


def run_checks(con_duck, con_state, interval, start_ms, end_ms, instrument_id="BTC-USDT",
               market_type="spot"):
    step = INTERVAL_MS[interval]
    first_aligned = -(-start_ms // step) * step  # ceil to step boundary
    rows = con_duck.execute(
        "SELECT open_time_utc, open, high, low, close, volume_base, volume_quote, is_final FROM klines "
        "WHERE exchange='okx' AND market_type=? AND instrument_id=? AND interval=? "
        "ORDER BY open_time_utc", [market_type, instrument_id, interval]).fetchall()
    scope = f"okx/{market_type}/{instrument_id}/{interval}"
    now = int(time.time() * 1000)
    anomalies = []

    def record(check_type, detail, ts=None):
        anomalies.append((check_type, ts, detail))
        con_state.execute(
            "INSERT INTO quality_anomalies(scope, anomaly_type, open_time_utc, detail, raw_batch_id, created_at_utc) "
            "VALUES (?,?,?,?,?,?)", (scope, check_type, ts, detail, None, now))

    ts_list = [r[0] for r in rows]
    actual = len(rows)
    # expected: final bars with open in [first_aligned, end_ms) whose close < end of last completed step
    last_expected_open = ((end_ms - 1) // step) * step - step  # last bar fully closed before end_ms... refined below
    # A bar is final iff close_time < now; the newest final bar open is floor((now-step)/step)*step
    newest_final_open = ((now - step) // step) * step if now % step == 0 else (now // step) * step - 0
    # simpler robust bound: bars with open+step-1 < end_ms and open >= first_aligned, excluding bars after last stored
    expected_opens = []
    t = first_aligned
    max_open = max(ts_list) if ts_list else None
    while t <= (max_open if max_open is not None else t - 1):
        expected_opens.append(t)
        t += step
    expected = len(expected_opens)
    ts_set = set(ts_list)
    missing = [t for t in expected_opens if t not in ts_set]
    for t in missing:
        record("gap", f"missing final kline at open_time_utc={t}", t)
    dups = actual - len(ts_set)
    if dups:
        record("duplicate", f"{dups} duplicate open_time rows in normalized table")
    bad_ohlc = 0
    bad_vol = 0
    unfinal = 0
    misaligned = 0
    for (ts, o, h, l, c, vb, vq, is_final) in rows:
        if not (l <= o <= h and l <= c <= h and l <= h):
            bad_ohlc += 1
            record("ohlc", f"ts={ts} o={o} h={h} l={l} c={c}", ts)
        if vb is not None and vb < 0 or (vq is not None and vq < 0):
            bad_vol += 1
            record("negative_volume", f"ts={ts} vb={vb} vq={vq}", ts)
        if not is_final:
            unfinal += 1
            record("unfinal_in_normalized", f"ts={ts}", ts)
        if ts % step != 0:
            misaligned += 1
            record("misaligned_timestamp", f"ts={ts} not aligned to {interval}", ts)
    summary = {"interval": interval, "actual_rows": actual, "expected_rows_window": expected,
               "missing": len(missing), "duplicates_in_table": dups,
               "bad_ohlc": bad_ohlc, "bad_volume": bad_vol, "unfinal": unfinal,
               "misaligned": misaligned,
               "first_open_utc": ts_list[0] if ts_list else None,
               "last_open_utc": ts_list[-1] if ts_list else None}
    con_state.execute(
        "INSERT INTO quality_checks(scope, check_type, checked_range, anomaly_count, detail_json, created_at_utc) "
        "VALUES (?,?,?,?,?,?)",
        (scope, "full_suite", f"{start_ms}-{end_ms}", len(anomalies), json.dumps(summary), now))
    con_state.commit()
    return summary
