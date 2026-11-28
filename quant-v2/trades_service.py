#!/usr/bin/env python3
"""Quant system v2 - trades (tick) collector.

Four WebSocket trade streams (OKX + Binance x spot + USDT swap),
BTC-USDT only, written as unified-caliber JSONL, partitioned by
day/hour under data/trades/<exchange>/<market_type>/<YYYY-MM-DD>/.
The current hour is written as plain <HH>.jsonl (crash-safe: an
unclean kill can only tear the last line); finished hours are
gzip-compressed and verified (<HH>.jsonl.gz) by rotation.

Record: {exchange, market_type, symbol, trade_id, trade_time_utc (ms),
         price, qty (ALWAYS base asset; OKX swap sz is contracts and is
         multiplied by ctVal 0.01), side (aggressor/taker side)}

Reliability: per-stream last_trade_id is anchored to the archive tail
at startup and tracked live; on (re)connect the gap is backfilled over
REST before live messages are accepted (live ids <= last_id are
dropped as duplicates). Disk floor guard: free space < 8 GiB suspends
writes and logs an alarm (Cl's >=8 GiB floor rule); resumes at 8.5.
Stats: logs/trades-stats.json rewritten every 60 s.
"""
import asyncio, gzip, json, os, shutil, time, urllib.request, urllib.parse

for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

BASE = "/opt/quant-system"
DATA_DIR = os.path.join(BASE, "data", "trades")
STATS_PATH = os.path.join(BASE, "logs", "trades-stats.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
DISK_FLOOR = 8 * 1024**3
DISK_RESUME = int(8.5 * 1024**3)
CTVAL = 0.01  # BTC-USDT-SWAP contract value in BTC

# ---------------- cleaning gates (2026-11-28, Cl's order) ----------------
# Live records pass three gates before archiving:
#  (1) timestamp plausibility - magnitude (microseconds passed off as
#      milliseconds), >5 min in the future, or >5 min stale vs the local
#      clock -> quarantined under data/quarantine/trades/, never archived
#      as clean. Backfilled records skip the drift checks (a legitimate
#      backfill is old by definition) but keep the magnitude check.
#  (2) value sanity - price <= 0 or qty <= 0 -> quarantined.
#  (3) peer cross-check - a live price >2% away from the OTHER exchange's
#      latest price for the same market (fresh <60 s) is archived WITH a
#      "suspect" flag (evidence kept, dirt marked, nothing silently cut).
TS_TOLERANCE_MS = 5 * 60_000
PEER_DEV_LIMIT = 0.02
LAST_PRICE = {}  # (exchange, market_type) -> (price, received_ms)
QUAR_DIR = os.path.join(BASE, "data", "quarantine", "trades")
LASTPRICE_PATH = os.path.join(BASE, "logs", "last-prices.json")


def ts_anomaly_reason(ts, nm):
    """None if the source timestamp is plausible as milliseconds."""
    if ts >= nm * 100:        # microseconds (or worse) passed off as ms
        return "ts_magnitude"
    if ts - nm > TS_TOLERANCE_MS:
        return "ts_future"
    if nm - ts > TS_TOLERANCE_MS:
        return "ts_stale"
    return None


def quarantine_trade(ex, mk, reason, rec):
    d = os.path.join(QUAR_DIR, f"{ex}-{mk}")
    os.makedirs(d, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    rec = dict(rec, reason=reason, received_ms=now_ms())
    with open(os.path.join(d, day + ".jsonl"), "a", buffering=1) as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")

from websockets.asyncio.client import connect as ws_connect


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def now_ms():
    return int(time.time() * 1000)


disk_suspended = False


async def disk_guard():
    global disk_suspended
    while True:
        free = shutil.disk_usage("/").free
        if not disk_suspended and free < DISK_FLOOR:
            disk_suspended = True
            log(f"ALARM_DISK_FLOOR free={free/1024**3:.2f} GiB < 8 GiB -> trade writes SUSPENDED")
        elif disk_suspended and free >= DISK_RESUME:
            disk_suspended = False
            log(f"DISK_RECOVERED free={free/1024**3:.2f} GiB -> trade writes resumed")
        await asyncio.sleep(60)


# ---------------- archive files ----------------

def _compress_file(src):
    """gzip a finished plain file -> .jsonl.gz (verified, then src removed)."""
    dst = src + ".gz"
    if os.path.exists(dst):
        dst = src + ".part2.gz"
    tmp = dst + ".tmp"
    n = 0
    with open(src, "rb") as fin, gzip.open(tmp, "wb", compresslevel=6) as fout:
        for line in fin:
            fout.write(line)
            n += 1
    with gzip.open(tmp, "rt") as f:
        m = sum(1 for _ in f)
    assert m == n, f"verify failed {src}: {m} != {n}"
    os.replace(tmp, dst)
    os.remove(src)
    return dst


def rotate_stale(root, current_key):
    """Compress every plain .jsonl whose hour is not the current hour."""
    if not os.path.isdir(root):
        return
    for day in os.listdir(root):
        d = os.path.join(root, day)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".jsonl") and f"{day}T{fn[:2]}" != current_key:
                try:
                    _compress_file(os.path.join(d, fn))
                except Exception as e:
                    log(f"ROTATE FAILED {fn}: {e}")


class HourWriter:
    """Current hour as plain .jsonl (line-buffered); rotation gzips
    finished hours. A kill can only tear the last plain line."""

    def __init__(self, root):
        self.root = root
        self.hour_key, self.fh = None, None

    def _open(self, ts):
        tm = time.gmtime(ts / 1000)
        day = time.strftime("%Y-%m-%d", tm)
        d = os.path.join(self.root, day)
        os.makedirs(d, exist_ok=True)
        self.hour_key = f"{day}T{time.strftime('%H', tm)}"
        rotate_stale(self.root, self.hour_key)
        self.fh = open(os.path.join(d, time.strftime("%H", tm) + ".jsonl"),
                       "a", buffering=1)

    def write_lines(self, lines, ts):
        key = time.strftime("%Y-%m-%dT%H", time.gmtime(ts / 1000))
        if key != self.hour_key:
            if self.fh:
                self.fh.close()
            self._open(ts)
        for line in lines:
            self.fh.write(line + "\n")
        self.fh.flush()


def archive_tail_id(ex, mk):
    """Last archived trade_id for a stream (restart recovery anchor)."""
    root = os.path.join(DATA_DIR, ex, mk)
    if not os.path.isdir(root):
        return 0
    cands = []
    for day in os.listdir(root):
        d = os.path.join(root, day)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".jsonl") or fn.endswith(".jsonl.gz"):
                cands.append((day, fn[:2], fn.endswith(".jsonl"),
                              os.path.join(d, fn)))
    for (_day, _hh, _plain, path) in sorted(cands, reverse=True):
        try:
            last = None
            if path.endswith(".gz"):
                with gzip.open(path, "rt") as f:
                    for line in f:
                        if line.strip():
                            last = line
            else:
                with open(path) as f:
                    for line in f:
                        if line.strip():
                            last = line
            if last:
                return int(json.loads(last)["trade_id"])
        except Exception:
            continue
    return 0


def http_json(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ---------------- backfill ----------------

def backfill_okx(inst_id, last_id):
    """Trades with id > last_id, ascending. /market/trades-history
    returns 404 from this egress (verified 2026-11-28: spot + swap,
    all parameter variants), so backfill uses /market/trades
    (latest 500). A gap wider than that page is logged GAP_UNFILLED
    with the missing id range - never silently skipped, never faked."""
    d = http_json("https://www.okx.com/api/v5/market/trades",
                  {"instId": inst_id, "limit": "500"})
    if d.get("code") != "0":
        raise RuntimeError(f"OKX backfill {d.get('code')} {d.get('msg')}")
    rows = [r for r in d["data"] if int(r["tradeId"]) > last_id]
    if rows:
        first = min(int(r["tradeId"]) for r in rows)
        if first > last_id + 1:
            log(f"GAP_UNFILLED okx {inst_id}: ids {last_id + 1}..{first - 1} "
                f"beyond the latest-500 page")
    rows.sort(key=lambda r: int(r["tradeId"]))
    return rows


def backfill_binance(market, last_id):
    # Spot: /api/v3/trades rejects fromId (-1101 "too many parameters");
    # /api/v3/historicalTrades on api.binance.com serves fromId WITHOUT
    # an API key from this egress (verified 2026-11-28); the .vision
    # data host does not serve that path at all.
    host = ("https://api.binance.com/api/v3/historicalTrades" if market == "spot"
            else "https://fapi.binance.com/fapi/v1/trades")
    out, nxt, iters = [], last_id + 1, 0
    while iters < 100:  # up to 100k trades per reconnect (~45 min of flow)
        rows = http_json(host, {"symbol": "BTCUSDT", "fromId": str(nxt), "limit": "1000"})
        if not rows:
            break
        iters += 1
        out.extend(rows)
        nxt = int(rows[-1]["id"]) + 1
        if len(rows) < 1000:
            break
    else:
        log(f"GAP_UNFILLED binance {market}: backfill capped after id {last_id}")
    return out


# ---------------- streams ----------------

class TradeStream:
    def __init__(self, ex, mk):
        self.ex, self.mk = ex, mk
        self.name = f"{ex}/{mk}"
        self.writer = HourWriter(os.path.join(DATA_DIR, ex, mk))
        self.last_id = archive_tail_id(ex, mk)
        if self.last_id:
            log(f"{self.name} resume anchor from archive: last_id={self.last_id}")
        self.stats = {"received": 0, "written": 0, "deduped": 0,
                      "backfilled": 0, "reconnects": 0,
                      "ts_quarantined": 0, "invalid_quarantined": 0,
                      "suspect": 0}
        self.buf = []
        self.last_flush = time.time()

    def emit(self, trade_id, ts, price, qty, side, live=True):
        if trade_id <= self.last_id:
            self.stats["deduped"] += 1
            return
        nm = now_ms()
        if live:
            reason = ts_anomaly_reason(ts, nm)
        else:  # backfill: only the magnitude check applies
            reason = "ts_magnitude" if ts >= nm * 100 else None
        if reason is None and (price <= 0 or qty <= 0):
            reason = "invalid_value"
        if reason is not None:
            quarantine_trade(self.ex, self.mk, reason, {
                "exchange": self.ex, "market_type": self.mk, "symbol": "BTC-USDT",
                "trade_id": trade_id, "trade_time_utc": ts,
                "price": price, "qty": qty, "side": side})
            key = "ts_quarantined" if reason.startswith("ts_") else "invalid_quarantined"
            self.stats[key] += 1
            log(f"{self.name} QUARANTINE {reason} trade_id={trade_id} ts={ts} px={price}")
            return
        self.last_id = trade_id
        # recv_ms = local arrival time. Every record carries BOTH clocks:
        # trade_time_utc is when the exchange says it happened, recv_ms is
        # when the data actually became usable here - the difference is
        # the latency a live strategy would really face (anti-lookahead).
        rec = {"exchange": self.ex, "market_type": self.mk, "symbol": "BTC-USDT",
               "trade_id": trade_id, "trade_time_utc": ts, "recv_ms": nm,
               "price": price, "qty": qty, "side": side}
        if live:
            peer = LAST_PRICE.get(
                ("binance" if self.ex == "okx" else "okx", self.mk))
            if peer and nm - peer[1] < 60_000 and peer[0] > 0:
                dev = abs(price - peer[0]) / peer[0]
                if dev > PEER_DEV_LIMIT:
                    rec["suspect"] = f"peer_dev_{dev * 100:.1f}pct"
                    self.stats["suspect"] += 1
            LAST_PRICE[(self.ex, self.mk)] = (price, nm)
        self.buf.append(json.dumps(rec, separators=(",", ":")))
        self.stats["written"] += 1
        if len(self.buf) >= 200 or time.time() - self.last_flush > 2:
            self.flush()

    def flush(self):
        if self.buf and not disk_suspended:
            self.writer.write_lines(self.buf, now_ms())
            self.buf = []
            self.last_flush = time.time()
        elif self.buf and disk_suspended:
            self.buf = []  # floor reached: drop rather than write

    # ---- OKX ----
    def okx_record(self, r, from_backfill=False):
        qty = float(r["sz"]) * (CTVAL if self.mk == "swap" else 1.0)
        self.emit(int(r["tradeId"]), int(r["ts"]), float(r["px"]), qty, r["side"],
                  live=not from_backfill)
        if from_backfill:
            self.stats["backfilled"] += 1

    async def run_okx(self):
        inst = "BTC-USDT" if self.mk == "spot" else "BTC-USDT-SWAP"
        while True:
            try:
                async with ws_connect("wss://ws.okx.com:443/ws/v5/public",
                                      ping_interval=None) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": [
                        {"channel": "trades", "instId": inst}]}))
                    log(f"{self.name} connected")
                    if self.last_id:
                        for r in await asyncio.to_thread(backfill_okx, inst, self.last_id):
                            self.okx_record(r, from_backfill=True)
                        log(f"{self.name} backfill done, last_id={self.last_id}")
                    last_ping = time.time()
                    async for msg in ws:
                        if msg == "pong":
                            continue
                        if time.time() - last_ping > 20:
                            await ws.send("ping")
                            last_ping = time.time()
                        d = json.loads(msg)
                        for r in d.get("data", []):
                            self.stats["received"] += 1
                            self.okx_record(r)
                        self.flush()
            except Exception as e:
                self.stats["reconnects"] += 1
                log(f"{self.name} WS error: {e} -> reconnect in 5s")
                self.flush()
                await asyncio.sleep(5)

    # ---- Binance ----
    def bin_record(self, r, from_backfill=False):
        self.emit(int(r["id"] if "id" in r else r["t"]),
                  int(r["time"] if "time" in r else r["T"]),
                  float(r["price"] if "price" in r else r["p"]),
                  float(r["qty"] if "qty" in r else r["q"]),
                  "sell" if (r["isBuyerMaker"] if "isBuyerMaker" in r else r["m"]) else "buy",
                  live=not from_backfill)
        if from_backfill:
            self.stats["backfilled"] += 1

    async def run_binance(self):
        host = ("wss://data-stream.binance.vision/ws/btcusdt@trade" if self.mk == "spot"
                else "wss://fstream.binance.com/ws/btcusdt@trade")
        while True:
            try:
                async with ws_connect(host, ping_interval=20) as ws:
                    log(f"{self.name} connected")
                    if self.last_id:
                        for r in await asyncio.to_thread(backfill_binance, self.mk, self.last_id):
                            self.bin_record(r, from_backfill=True)
                        log(f"{self.name} backfill done, last_id={self.last_id}")
                    async for msg in ws:
                        d = json.loads(msg)
                        if d.get("e") != "trade":
                            continue
                        self.stats["received"] += 1
                        self.bin_record(d)
                        self.flush()
            except Exception as e:
                self.stats["reconnects"] += 1
                log(f"{self.name} WS error: {e} -> reconnect in 5s")
                self.flush()
                await asyncio.sleep(5)

    async def run(self):
        if self.ex == "okx":
            await self.run_okx()
        else:
            await self.run_binance()


async def lastprice_loop():
    """Shared latest-price file: the books service cross-checks its
    snapshot mid-prices against these (and only these) trade prices."""
    while True:
        await asyncio.sleep(5)
        snap = {f"{ex}/{mk}": {"price": p, "ts": t}
                for (ex, mk), (p, t) in LAST_PRICE.items()}
        tmp = LASTPRICE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, LASTPRICE_PATH)


async def stats_loop(streams):
    t0 = time.time()
    while True:
        await asyncio.sleep(60)
        snap = {"ts": now_ms(), "uptime_s": int(time.time() - t0),
                "disk_suspended": disk_suspended,
                "free_gib": round(shutil.disk_usage("/").free / 1024**3, 2),
                "streams": {s.name: {**s.stats, "last_id": s.last_id} for s in streams}}
        tmp = STATS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, STATS_PATH)


async def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    streams = [TradeStream(ex, mk) for ex in ("okx", "binance") for mk in ("spot", "swap")]
    log("trades service start: 4 streams")
    await asyncio.gather(disk_guard(), stats_loop(streams), lastprice_loop(),
                         *[s.run() for s in streams])


if __name__ == "__main__":
    asyncio.run(main())
