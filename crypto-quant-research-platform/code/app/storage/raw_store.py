"""Immutable raw storage: one .json.gz per API page + per-batch manifest (.manifest.json) with SHA-256."""
import gzip
import hashlib
import json
import os
import uuid


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_raw_batch(base_dir, exchange, market_type, instrument_id, interval,
                   received_ms, request_params, raw_rows):
    date_dir = time_dir = None
    from datetime import datetime, timezone
    date_dir = datetime.fromtimestamp(received_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    out_dir = os.path.join(base_dir, f"exchange={exchange}", f"market_type={market_type}",
                           "data_type=kline", f"instrument_id={instrument_id}",
                           f"interval={interval}", f"date={date_dir}")
    os.makedirs(out_dir, exist_ok=True)
    batch_id = f"{received_ms}-{uuid.uuid4().hex[:8]}"
    final_path = os.path.join(out_dir, f"batch-{batch_id}.json.gz")
    tmp_path = final_path + ".tmp"
    payload = {"meta": {"exchange": exchange, "market_type": market_type,
                        "instrument_id": instrument_id, "interval": interval,
                        "received_at_utc": received_ms, "request_params": request_params,
                        "source": "rest:market/history-candles"},
               "data": raw_rows}
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, final_path)  # atomic rename
    sha = _sha256_file(final_path)
    ts_list = [int(r[0]) for r in raw_rows] if raw_rows else []
    final_rows = [r for r in raw_rows if str(r[8]) == "1"]
    manifest = {"batch_id": batch_id, "path": final_path, "sha256": sha,
                "rows_total": len(raw_rows), "rows_final": len(final_rows),
                "min_open_time_utc": min(ts_list) if ts_list else None,
                "max_open_time_utc": max(ts_list) if ts_list else None,
                "received_at_utc": received_ms}
    with open(final_path + ".manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest
