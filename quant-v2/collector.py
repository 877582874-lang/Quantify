#!/usr/bin/env python3
"""Quant system v2 - unified-caliber market data collector with
dual-source cross-validation.

Pure stdlib. Collects FINAL (closed) candlesticks + funding rates from
OKX and Binance (spot + USDT perpetual swap) into one SQLite database
with ONE canonical schema for every source (see README).

CROSS-VALIDATION (Cl, 2026-11-28): the two exchanges check each other.
No bar enters `klines` on one exchange's say-so alone:
  fetch -> staging -> pair OKX vs Binance (same market/interval/ts)
  -> close-price deviation <= 0.10% : BOTH bars promoted into klines
  -> deviation  > 0.10%             : BOTH bars quarantined in
                                      `anomalies` (reason=price_mismatch),
                                      never enter klines, logged at once
  -> counterpart missing > 15 min   : quarantined (counterpart_missing)
  -> bar fails OHLC sanity          : quarantined (ohlc_sanity)
Threshold basis: measured 2026-11-28 on 2,484 live pairs - mean dev
0.005%, p99 0.021%, max 0.024%; 0.10% is ~4x the worst healthy pair.
Quarantined bars keep their full OHLCV in `anomalies` for audit/recovery.
Funding rates are exchange-specific by nature and are NOT pair-gated.
"""
import json, os, sqlite3, time, urllib.request, urllib.parse

for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "market.db")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

EXCHANGES = ("okx", "binance")
MARKETS = ("spot", "swap")
SYMBOLS = ("BTC-USDT",)
INTERVALS = ("1m", "5m", "15m", "1h", "4h")
OKX_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}
DEV_LIMIT = 0.0010          # 0.10% max allowed cross-exchange close deviation
PAIR_GRACE_MS = 15 * 60_000  # how long a staged bar waits for its counterpart
FLOOR_MS = None             # set in main() from collector_meta / first-init


def now_ms():
    return int(time.time() * 1000)


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def http_json(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


# ---------------- OKX ----------------

def okx_inst(symbol, market):
    return symbol if market == "spot" else symbol + "-SWAP"


def okx_fetch(symbol, market, interval, after=None, limit=100):
    p = {"instId": okx_inst(symbol, market), "bar": OKX_BAR[interval], "limit": str(limit)}
    if after is not None:
        p["after"] = str(after)
    d = http_json("https://www.okx.com/api/v5/market/history-candles", p)
    if d.get("code") != "0":
        raise RuntimeError(f"OKX {d.get('code')} {d.get('msg')}")
    out = []
    for f in d["data"]:
        # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        if f[8] != "1":
            continue
        vol_base = float(f[5]) if market == "spot" else float(f[6])
        out.append((int(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4]),
                    vol_base, float(f[7]) if f[7] else None))
    return out


def okx_funding(symbol):
    d = http_json("https://www.okx.com/api/v5/public/funding-rate-history",
                  {"instId": okx_inst(symbol, "swap"), "limit": "20"})
    if d.get("code") != "0":
        raise RuntimeError(f"OKX funding {d.get('code')}")
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in d["data"]]


# ---------------- Binance ----------------

def bin_sym(symbol):
    return symbol.replace("-", "")


def bin_fetch(symbol, market, interval, end_time=None, limit=1000):
    host = ("https://data-api.binance.vision/api/v3/klines" if market == "spot"
            else "https://fapi.binance.com/fapi/v1/klines")
    p = {"symbol": bin_sym(symbol), "interval": interval, "limit": str(limit)}
    if end_time is not None:
        p["endTime"] = str(end_time)
    rows = http_json(host, p)
    out = []
    nm = now_ms()
    for f in rows:
        # [openTime, o, h, l, c, volume(base), closeTime, quoteVolume, ...]
        if int(f[6]) > nm:
            continue
        out.append((int(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4]),
                    float(f[5]), float(f[7])))
    return out


def bin_funding(symbol):
    rows = http_json("https://fapi.binance.com/fapi/v1/fundingRate",
                     {"symbol": bin_sym(symbol), "limit": "20"})
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in rows]


FETCH = {"okx": okx_fetch, "binance": bin_fetch}
FUNDING = {"okx": okx_funding, "binance": bin_funding}

# ---------------- storage ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
  exchange TEXT NOT NULL, market_type TEXT NOT NULL, symbol TEXT NOT NULL,
  interval TEXT NOT NULL, open_time_utc INTEGER NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume_base REAL NOT NULL, volume_quote REAL,
  PRIMARY KEY (exchange, market_type, symbol, interval, open_time_utc)
);
CREATE TABLE IF NOT EXISTS funding_rates (
  exchange TEXT NOT NULL, symbol TEXT NOT NULL,
  funding_time_utc INTEGER NOT NULL, funding_rate REAL NOT NULL,
  PRIMARY KEY (exchange, symbol, funding_time_utc)
);
CREATE TABLE IF NOT EXISTS collector_meta (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS staging (
  exchange TEXT NOT NULL, market_type TEXT NOT NULL, symbol TEXT NOT NULL,
  interval TEXT NOT NULL, open_time_utc INTEGER NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume_base REAL NOT NULL, volume_quote REAL,
  staged_at_ms INTEGER NOT NULL,
  PRIMARY KEY (exchange, market_type, symbol, interval, open_time_utc)
);
CREATE TABLE IF NOT EXISTS anomalies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  detected_ms INTEGER NOT NULL, reason TEXT NOT NULL,
  exchange TEXT NOT NULL, market_type TEXT NOT NULL, symbol TEXT NOT NULL,
  interval TEXT NOT NULL, open_time_utc INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume_base REAL, volume_quote REAL,
  counterpart_close REAL, deviation_pct REAL
);
"""


def db_open():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    return db


def get_floor(db):
    row = db.execute("SELECT value FROM collector_meta WHERE key='collect_floor_ms'").fetchone()
    if row:
        return int(row[0])
    CST = 8 * 3600_000
    floor = ((now_ms() + CST) // 86_400_000) * 86_400_000 - CST
    db.execute("INSERT OR REPLACE INTO collector_meta VALUES ('collect_floor_ms', ?)",
               (str(floor),))
    db.commit()
    return floor


def quarantine(db, reason, ex, mk, sym, iv, row, counterpart_close=None, dev=None):
    t, o, h, l, c, vb, vq = row
    db.execute(
        "INSERT INTO anomalies (detected_ms, reason, exchange, market_type, symbol,"
        " interval, open_time_utc, open, high, low, close, volume_base, volume_quote,"
        " counterpart_close, deviation_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (now_ms(), reason, ex, mk, sym, iv, t, o, h, l, c, vb, vq,
         counterpart_close, dev))


def stage_bars(db, ex, mk, sym, iv, rows):
    """Fetched final bars land in staging (never directly in klines)."""
    nm = now_ms()
    for row in rows:
        t, o, h, l, c, vb, vq = row
        if t < FLOOR_MS:
            continue
        if not (l <= min(o, c) and max(o, c) <= h and l <= h and vb >= 0):
            quarantine(db, "ohlc_sanity", ex, mk, sym, iv, row)
            log(f"ANOMALY ohlc_sanity {ex}/{mk}/{sym}/{iv} ts={t} o={o} h={h} l={l} c={c}")
            continue
        db.execute(
            "INSERT OR IGNORE INTO staging VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ex, mk, sym, iv, t, o, h, l, c, vb, vq, nm))
    db.commit()


def promote(db):
    """Pair staged OKX/Binance bars; promote agreements, quarantine the rest."""
    pairs = db.execute("""
        SELECT a.market_type, a.symbol, a.interval, a.open_time_utc,
               a.open, a.high, a.low, a.close, a.volume_base, a.volume_quote,
               b.open, b.high, b.low, b.close, b.volume_base, b.volume_quote
        FROM staging a JOIN staging b
          ON a.market_type=b.market_type AND a.symbol=b.symbol
         AND a.interval=b.interval AND a.open_time_utc=b.open_time_utc
        WHERE a.exchange='okx' AND b.exchange='binance'""").fetchall()
    promoted = 0
    for (mk, sym, iv, t, ao, ah, al, ac, avb, avq, bo, bh, bl, bc, bvb, bvq) in pairs:
        dev = abs(ac - bc) / ((ac + bc) / 2)
        a_row = (t, ao, ah, al, ac, avb, avq)
        b_row = (t, bo, bh, bl, bc, bvb, bvq)
        if dev <= DEV_LIMIT:
            for ex, row in (("okx", a_row), ("binance", b_row)):
                db.execute("INSERT OR IGNORE INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (ex, mk, sym, iv) + row)
                db.execute("DELETE FROM staging WHERE exchange=? AND market_type=?"
                           " AND symbol=? AND interval=? AND open_time_utc=?",
                           (ex, mk, sym, iv, t))
            promoted += 2
        else:
            quarantine(db, "price_mismatch", "okx", mk, sym, iv, a_row, bc, dev * 100)
            quarantine(db, "price_mismatch", "binance", mk, sym, iv, b_row, ac, dev * 100)
            for ex in EXCHANGES:
                db.execute("DELETE FROM staging WHERE exchange=? AND market_type=?"
                           " AND symbol=? AND interval=? AND open_time_utc=?",
                           (ex, mk, sym, iv, t))
            log(f"ANOMALY price_mismatch {mk}/{sym}/{iv} ts={t} "
                f"okx_close={ac} binance_close={bc} dev={dev*100:.3f}% -> quarantined, NOT stored")
    cutoff = now_ms() - PAIR_GRACE_MS
    stale = db.execute("SELECT exchange, market_type, symbol, interval, open_time_utc,"
                       " open, high, low, close, volume_base, volume_quote"
                       " FROM staging WHERE staged_at_ms < ?", (cutoff,)).fetchall()
    for (ex, mk, sym, iv, t, o, h, l, c, vb, vq) in stale:
        quarantine(db, "counterpart_missing", ex, mk, sym, iv, (t, o, h, l, c, vb, vq))
        db.execute("DELETE FROM staging WHERE exchange=? AND market_type=?"
                   " AND symbol=? AND interval=? AND open_time_utc=?",
                   (ex, mk, sym, iv, t))
        log(f"ANOMALY counterpart_missing {ex}/{mk}/{sym}/{iv} ts={t} -> quarantined, NOT stored")
    db.commit()
    return promoted


def insert_funding(db, ex, sym, rows):
    rows = [r for r in rows if r[0] >= FLOOR_MS]
    db.executemany("INSERT OR IGNORE INTO funding_rates VALUES (?,?,?,?)",
                   [(ex, sym, t, r) for (t, r) in rows])
    db.commit()


# ---------------- seeding (floor-bounded, pair-gated) + polling ----------------

def seed_pair(db, mk, sym, iv):
    cursors = {"okx": None, "binance": None}
    done = {"okx": False, "binance": False}
    while not all(done.values()):
        for ex in EXCHANGES:
            if done[ex]:
                continue
            try:
                if ex == "okx":
                    rows = okx_fetch(sym, mk, iv, after=cursors[ex])
                else:
                    rows = bin_fetch(sym, mk, iv, end_time=cursors[ex])
            except Exception as e:
                log(f"seed FAILED {ex}/{mk}/{sym}/{iv}: {e}")
                done[ex] = True
                continue
            rows = [r for r in rows if r[0] >= FLOOR_MS]
            if rows:
                stage_bars(db, ex, mk, sym, iv, rows)
                cursors[ex] = min(r[0] for r in rows) - (1 if ex == "binance" else 0)
            if len(rows) < 90 or (rows and min(r[0] for r in rows) <= FLOOR_MS):
                done[ex] = True
            time.sleep(0.15)
    return promote(db)


def poll_pair(db, mk, sym, iv):
    for ex in EXCHANGES:
        rows = FETCH[ex](sym, mk, iv, limit=8)
        if rows:
            stage_bars(db, ex, mk, sym, iv, rows)
    return promote(db)


def main():
    global FLOOR_MS
    db = db_open()
    FLOOR_MS = get_floor(db)
    pairs = [(mk, sym, iv) for mk in MARKETS for sym in SYMBOLS for iv in INTERVALS]
    log(f"collector start: {len(pairs)*2} series (pair-gated), floor={FLOOR_MS}, db={DB_PATH}")
    for (mk, sym, iv) in pairs:
        n = seed_pair(db, mk, sym, iv)
        log(f"seed {mk}/{sym}/{iv}: promoted {n} bars")
    for sym in SYMBOLS:
        for ex in EXCHANGES:
            try:
                insert_funding(db, ex, sym, FUNDING[ex](sym))
            except Exception as e:
                log(f"funding seed FAILED {ex}/{sym}: {e}")
    log("seeding done; live polling every 60s")
    cycle = 0
    while True:
        t0 = time.time()
        cycle += 1
        errs = 0
        for (mk, sym, iv) in pairs:
            try:
                poll_pair(db, mk, sym, iv)
            except Exception as e:
                errs += 1
                log(f"poll FAILED {mk}/{sym}/{iv}: {e}")
            time.sleep(0.1)
        if cycle % 60 == 1:
            for sym in SYMBOLS:
                for ex in EXCHANGES:
                    try:
                        insert_funding(db, ex, sym, FUNDING[ex](sym))
                    except Exception as e:
                        log(f"funding poll FAILED {ex}/{sym}: {e}")
        db.execute("INSERT OR REPLACE INTO collector_meta VALUES ('last_cycle', ?)",
                   (str(now_ms()),))
        db.execute("INSERT OR REPLACE INTO collector_meta VALUES ('last_cycle_errors', ?)",
                   (str(errs),))
        db.commit()
        wait = 60 - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)


if __name__ == "__main__":
    main()
