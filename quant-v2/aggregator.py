#!/usr/bin/env python3
"""Quant system v2 - aggregation + alignment layer.

Reads the raw archives (trades + book snapshots) and the kline DB and
builds research-ready derived data in data/derived.db:

TIME BARS from trades (per exchange/market): trade_bars_1s / _1m / _5m
  {open_ms, close_ms, OHLC, volume_base, volume_quote, trades,
   buy_volume, sell_volume, suspect_trades}
VOLUME BARS: one bar per 10 BTC of base volume (volume_bars).
DOLLAR BARS: one bar per 1,000,000 USDT of quote volume (dollar_bars).
  A trade that straddles a boundary is split at the boundary price.
BOOK FEATURES per 1m from snapshots (book_features_1m): last mid,
  mean/max spread (bps), mean top-10 depth imbalance, mean top-10
  depth per side, snapshot counts (incl. suspect-flagged ones).
ALIGNED tables (aligned_1m / aligned_5m): kline closes for both
  exchanges + funding rate and book mid/spread AS OF the bar close.

ANTI-LOOKAHEAD CONTRACT (the whole point of close_ms / available_ms):
  - Bars are keyed by their OPEN time; a bar's data is complete only
    at close_ms = open_ms + window. Signals may use it from close_ms.
  - aligned_* funding/book values are the last values with timestamp
    <= close_ms (as-of join) - never anything from after the close.
  - aligned_*.available_ms = when BOTH gated klines were promoted;
    a point-in-time backtest must use max(close_ms, available_ms).
  - Canonical execution caliber: signal on final-bar close, execute
    at the NEXT bar's open. Live triggers on unfinished bars break it.

State (watermarks + partial bars) lives in agg_meta inside derived.db;
processing is id-ordered per stream and idempotent - a crash replays
at most one hour file, skipping trade ids already aggregated.
"""
import gzip, json, os, sqlite3, time

for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

BASE = "/opt/quant-system"
TRADES_DIR = os.path.join(BASE, "data", "trades")
BOOKS_DIR = os.path.join(BASE, "data", "books")
DERIVED_PATH = os.path.join(BASE, "data", "derived.db")
MARKET_PATH = os.path.join(BASE, "data", "market.db")
VOLUME_BAR_BTC = 10.0
DOLLAR_BAR_USDT = 1_000_000.0
TIME_WINDOWS = ((1000, "1s"), (60_000, "1m"), (300_000, "5m"))
EXCHANGES = ("okx", "binance")
MARKETS = ("spot", "swap")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def now_ms():
    return int(time.time() * 1000)


SCHEMA = """
CREATE TABLE IF NOT EXISTS agg_meta (key TEXT PRIMARY KEY, value TEXT);
"""
for _lbl in ("1s", "1m", "5m"):
    SCHEMA += f"""
CREATE TABLE IF NOT EXISTS trade_bars_{_lbl} (
  exchange TEXT NOT NULL, market_type TEXT NOT NULL,
  open_ms INTEGER NOT NULL, close_ms INTEGER NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume_base REAL, volume_quote REAL, trades INTEGER,
  buy_volume REAL, sell_volume REAL, suspect_trades INTEGER,
  PRIMARY KEY (exchange, market_type, open_ms));"""
for _t in ("volume_bars", "dollar_bars"):
    SCHEMA += f"""
CREATE TABLE IF NOT EXISTS {_t} (
  exchange TEXT NOT NULL, market_type TEXT NOT NULL, seq INTEGER NOT NULL,
  open_ms INTEGER, close_ms INTEGER,
  open REAL, high REAL, low REAL, close REAL,
  volume_base REAL, volume_quote REAL, trades INTEGER,
  PRIMARY KEY (exchange, market_type, seq));"""
SCHEMA += """
CREATE TABLE IF NOT EXISTS book_features_1m (
  exchange TEXT NOT NULL, market_type TEXT NOT NULL,
  open_ms INTEGER NOT NULL, close_ms INTEGER NOT NULL,
  mid_last REAL, spread_bps_mean REAL, spread_bps_max REAL,
  imbalance10_mean REAL, bid_depth10_mean REAL, ask_depth10_mean REAL,
  snapshots INTEGER, suspect_snapshots INTEGER,
  PRIMARY KEY (exchange, market_type, open_ms));
"""
for _iv in ("1m", "5m"):
    SCHEMA += f"""
CREATE TABLE IF NOT EXISTS aligned_{_iv} (
  market_type TEXT NOT NULL, open_ms INTEGER NOT NULL, close_ms INTEGER NOT NULL,
  available_ms INTEGER,
  okx_close REAL, binance_close REAL,
  okx_funding_asof REAL, binance_funding_asof REAL,
  okx_mid_asof REAL, binance_mid_asof REAL,
  okx_spread_bps_asof REAL, binance_spread_bps_asof REAL,
  PRIMARY KEY (market_type, open_ms));"""


def db_open():
    os.makedirs(os.path.dirname(DERIVED_PATH), exist_ok=True)
    db = sqlite3.connect(DERIVED_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    db.execute("ATTACH DATABASE ? AS mkt", (MARKET_PATH,))  # read-only by discipline: never written
    return db


def meta_get(db, key, default):
    row = db.execute("SELECT value FROM agg_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def meta_set(db, key, value):
    db.execute("INSERT OR REPLACE INTO agg_meta VALUES (?,?)",
               (key, json.dumps(value)))


def stream_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for day in sorted(os.listdir(root)):
        d = os.path.join(root, day)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if ".jsonl" in fn:
                out.append((f"{day}/{fn}", os.path.join(d, fn)))
    return out


def read_lines(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line


# ---------------- trade aggregation ----------------

def new_partial(ts, price):
    return {"open_ms": ts, "o": price, "h": price, "l": price, "c": price,
            "vb": 0.0, "vq": 0.0, "n": 0, "buy": 0.0, "sell": 0.0, "sus": 0}


def feed_time_bars(db, st, ex, mk, tr):
    ts, price, qty = tr["trade_time_utc"], tr["price"], tr["qty"]
    for w, lbl in TIME_WINDOWS:
        key = f"tb_{lbl}"
        p = st["partials"].get(key)
        win = ts - ts % w
        if p is not None and win > p["open_ms"]:
            db.execute(
                f"INSERT OR REPLACE INTO trade_bars_{lbl} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ex, mk, p["open_ms"], p["open_ms"] + w, p["o"], p["h"], p["l"],
                 p["c"], p["vb"], p["vq"], p["n"], p["buy"], p["sell"], p["sus"]))
            p = None
        if p is None:
            p = new_partial(win, price)
            st["partials"][key] = p
        p["h"] = max(p["h"], price)
        p["l"] = min(p["l"], price)
        p["c"] = price
        p["vb"] += qty
        p["vq"] += qty * price
        p["n"] += 1
        if tr["side"] == "buy":
            p["buy"] += qty
        else:
            p["sell"] += qty
        if "suspect" in tr:
            p["sus"] += 1


def feed_threshold_bar(db, st, ex, mk, tr, kind, threshold, by_quote):
    """Volume/dollar bar with boundary splitting at the trade price."""
    key = f"xb_{kind}"
    p = st["partials"].get(key)
    if p is None:
        p = new_partial(tr["trade_time_utc"], tr["price"])
        p["seq"] = st.get(f"{kind}_seq", 0)
        st["partials"][key] = p
    price, remaining = tr["price"], tr["qty"]
    first_chunk = True
    while remaining > 1e-12:
        if by_quote:
            space_base = (threshold - p["vq"]) / price
        else:
            space_base = threshold - p["vb"]
        take = min(remaining, space_base)
        p["h"] = max(p["h"], price)
        p["l"] = min(p["l"], price)
        p["c"] = price
        p["vb"] += take
        p["vq"] += take * price
        if first_chunk:
            p["n"] += 1
            first_chunk = False
        remaining -= take
        full = (p["vq"] >= threshold - 1e-6) if by_quote else (p["vb"] >= threshold - 1e-9)
        if full:
            db.execute(
                f"INSERT OR REPLACE INTO {kind}_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ex, mk, p["seq"], p["open_ms"], tr["trade_time_utc"], p["o"],
                 p["h"], p["l"], p["c"], p["vb"], p["vq"], p["n"]))
            st[f"{kind}_seq"] = p["seq"] + 1
            p = new_partial(tr["trade_time_utc"], price)
            p["seq"] = st[f"{kind}_seq"]
            st["partials"][key] = p


def process_trades(db, ex, mk):
    skey = f"trades:{ex}/{mk}"
    st = meta_get(db, skey, {"done_upto": None, "last_id": 0, "partials": {}})
    files = stream_files(os.path.join(TRADES_DIR, ex, mk))
    fed = 0
    for i, (fkey, path) in enumerate(files):
        if st["done_upto"] and fkey <= st["done_upto"]:
            continue
        finalized = path.endswith(".gz") or i < len(files) - 1
        for line in read_lines(path):
            try:
                tr = json.loads(line)
            except Exception:
                continue  # torn tail line of a live file
            if tr["trade_id"] <= st["last_id"]:
                continue
            st["last_id"] = tr["trade_id"]
            feed_time_bars(db, st, ex, mk, tr)
            feed_threshold_bar(db, st, ex, mk, tr, "volume", VOLUME_BAR_BTC, False)
            feed_threshold_bar(db, st, ex, mk, tr, "dollar", DOLLAR_BAR_USDT, True)
            fed += 1
        if finalized:
            st["done_upto"] = fkey
        meta_set(db, skey, st)
        db.commit()
    return fed


# ---------------- book feature aggregation ----------------

def process_books(db, ex, mk):
    skey = f"books:{ex}/{mk}"
    st = meta_get(db, skey, {"done_upto": None, "last_ts": 0, "acc": None})
    files = stream_files(os.path.join(BOOKS_DIR, ex, mk))
    fed = 0
    for i, (fkey, path) in enumerate(files):
        if st["done_upto"] and fkey <= st["done_upto"]:
            continue
        finalized = path.endswith(".gz") or i < len(files) - 1
        for line in read_lines(path):
            try:
                sn = json.loads(line)
            except Exception:
                continue
            ts = sn["ts_utc"]
            if ts <= st["last_ts"]:
                continue
            st["last_ts"] = ts
            if not sn["bids"] or not sn["asks"]:
                continue
            minute = ts - ts % 60_000
            acc = st["acc"]
            if acc is not None and minute > acc["minute"]:
                db.execute(
                    "INSERT OR REPLACE INTO book_features_1m VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ex, mk, acc["minute"], acc["minute"] + 60_000, acc["mid_last"],
                     acc["spread_sum"] / acc["n"], acc["spread_max"],
                     acc["imb_sum"] / acc["n"], acc["bid10_sum"] / acc["n"],
                     acc["ask10_sum"] / acc["n"], acc["n"], acc["sus"]))
                acc = None
            if acc is None:
                acc = {"minute": minute, "mid_last": 0.0, "spread_sum": 0.0,
                       "spread_max": 0.0, "imb_sum": 0.0, "bid10_sum": 0.0,
                       "ask10_sum": 0.0, "n": 0, "sus": 0}
                st["acc"] = acc
            bb, ba = sn["bids"][0][0], sn["asks"][0][0]
            mid = (bb + ba) / 2
            b10 = sum(q for _p, q in sn["bids"][:10])
            a10 = sum(q for _p, q in sn["asks"][:10])
            acc["mid_last"] = mid
            acc["spread_sum"] += (ba - bb) / mid * 10_000
            acc["spread_max"] = max(acc["spread_max"], (ba - bb) / mid * 10_000)
            acc["imb_sum"] += (b10 - a10) / (b10 + a10) if (b10 + a10) > 0 else 0.0
            acc["bid10_sum"] += b10
            acc["ask10_sum"] += a10
            acc["n"] += 1
            if "suspect" in sn:
                acc["sus"] += 1
            fed += 1
        if finalized:
            st["done_upto"] = fkey
        meta_set(db, skey, st)
        db.commit()
    return fed


# ---------------- alignment (as-of, no lookahead) ----------------

def build_aligned(db, iv, window_ms):
    """Rebuild the trailing 2 days of aligned rows from the gated
    klines; funding/book values are as of each bar's close_ms."""
    since = now_ms() - 2 * 86_400_000
    feat_minute = "k.open_time_utc" if iv == "1m" else "k.open_time_utc + 240000"
    rows = db.execute(f"""
        SELECT k.market_type, k.open_time_utc,
               MAX(CASE WHEN k.exchange='okx' THEN k.close END),
               MAX(CASE WHEN k.exchange='binance' THEN k.close END),
               MAX(k.available_ms),
               (SELECT f.funding_rate FROM mkt.funding_rates f
                 WHERE f.exchange='okx' AND f.funding_time_utc <= k.open_time_utc + {window_ms}
                 ORDER BY f.funding_time_utc DESC LIMIT 1),
               (SELECT f.funding_rate FROM mkt.funding_rates f
                 WHERE f.exchange='binance' AND f.funding_time_utc <= k.open_time_utc + {window_ms}
                 ORDER BY f.funding_time_utc DESC LIMIT 1),
               fo.mid_last, fb.mid_last, fo.spread_bps_mean, fb.spread_bps_mean
        FROM mkt.klines k
        LEFT JOIN book_features_1m fo
          ON fo.exchange='okx' AND fo.market_type=k.market_type
         AND fo.open_ms = {feat_minute}
        LEFT JOIN book_features_1m fb
          ON fb.exchange='binance' AND fb.market_type=k.market_type
         AND fb.open_ms = {feat_minute}
        WHERE k.interval=? AND k.open_time_utc >= ?
        GROUP BY k.market_type, k.open_time_utc""", (iv, since)).fetchall()
    for (mk, t, oc, bc, avail, ofund, bfund, omid, bmid, ospr, bspr) in rows:
        db.execute(
            f"INSERT OR REPLACE INTO aligned_{iv} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mk, t, t + window_ms, avail, oc, bc, ofund, bfund, omid, bmid, ospr, bspr))
    db.commit()
    return len(rows)


def main():
    db = db_open()
    log("aggregator start: trade bars (1s/1m/5m, volume 10 BTC, dollar 1M USDT), "
        "book features 1m, aligned 1m/5m (as-of close)")
    while True:
        t0 = time.time()
        try:
            tf = sum(process_trades(db, ex, mk) for ex in EXCHANGES for mk in MARKETS)
            bf = sum(process_books(db, ex, mk) for ex in EXCHANGES for mk in MARKETS)
            na = build_aligned(db, "1m", 60_000) + build_aligned(db, "5m", 300_000)
            if tf or bf:
                log(f"pass: trades +{tf}, snapshots +{bf}, aligned rows {na}")
        except Exception as e:
            log(f"pass FAILED: {e}")
        wait = 30 - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)


if __name__ == "__main__":
    main()
