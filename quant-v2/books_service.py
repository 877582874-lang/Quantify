#!/usr/bin/env python3
"""Quant system v2 - order book service.

Maintains four local order books (OKX + Binance x spot + USDT swap,
BTC-USDT) and writes periodic snapshots under
data/books/<exchange>/<market_type>/<YYYY-MM-DD>/: the current hour
is plain <HH>.jsonl (crash-safe), finished hours are gzip-compressed
and verified by rotation.
Snapshot: every 5 s per book, top 100 levels per side,
{ts_utc, exchange, market_type, symbol, bids:[[px, qty_base]...],
 asks:[[...]]} - qty ALWAYS base asset (the OKX swap book is held in
raw contract units for checksum fidelity, converted x0.01 on output).

Integrity rules (a book that fails validation is rebuilt, never trusted):
- Binance: REST snapshot + buffered diff stream. Spot: first event must
  straddle lastUpdateId+1, then U == prev_u + 1. Futures: the bridging
  event must satisfy pu <= lastUpdateId < u (depth deltas carry absolute
  quantities, so overlap is safe), then pu must chain to the previous u.
  Any break -> full resync.
- OKX: 'books' channel. Live-verified 2026-11-28: the checksum field is
  always 0 on this channel (60/60 messages), so the enforced integrity
  check is the seqId/prevSeqId chain (prevSeqId must equal the previous
  message's seqId); the CRC32 checksum is still verified whenever a
  non-zero value is ever provided. Break -> reconnect + fresh snapshot.
Disk floor guard: < 8 GiB free suspends snapshot writes + alarm log.
Stats: logs/books-stats.json every 60 s.
"""
import asyncio, gzip, json, os, shutil, time, urllib.request, urllib.parse, zlib

for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

BASE = "/opt/quant-system"
DATA_DIR = os.path.join(BASE, "data", "books")
STATS_PATH = os.path.join(BASE, "logs", "books-stats.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
DISK_FLOOR = 8 * 1024**3
DISK_RESUME = int(8.5 * 1024**3)
SNAP_EVERY = 5
SNAP_LEVELS = 100

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
            log(f"ALARM_DISK_FLOOR free={free/1024**3:.2f} GiB < 8 GiB -> snapshot writes SUSPENDED")
        elif disk_suspended and free >= DISK_RESUME:
            disk_suspended = False
            log(f"DISK_RECOVERED free={free/1024**3:.2f} GiB -> snapshot writes resumed")
        await asyncio.sleep(60)


# ---------------- archive files ----------------

def _compress_file(src):
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

    def write_line(self, line, ts):
        key = time.strftime("%Y-%m-%dT%H", time.gmtime(ts / 1000))
        if key != self.hour_key:
            if self.fh:
                self.fh.close()
            self._open(ts)
        self.fh.write(line + "\n")
        self.fh.flush()


def http_json(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


class Book:
    """Raw-unit book: {price_str: qty_str}; factor converts to base qty."""

    def __init__(self, factor):
        self.bids, self.asks = {}, {}
        self.factor = factor
        self.synced = False

    def apply_side(self, side, updates):
        book = self.bids if side == "b" else self.asks
        for px, qty in updates:
            if float(qty) == 0:
                book.pop(px, None)
            else:
                book[px] = qty

    def load_snapshot(self, bids, asks):
        self.bids = {px: q for px, q in bids}
        self.asks = {px: q for px, q in asks}
        self.synced = True

    def top(self, side, n):
        book = self.bids if side == "b" else self.asks
        items = sorted(book.items(), key=lambda kv: float(kv[0]),
                       reverse=(side == "b"))[:n]
        return [[float(px), float(q) * self.factor] for px, q in items]

    def okx_checksum(self):
        bids = sorted(self.bids.items(), key=lambda kv: float(kv[0]), reverse=True)[:25]
        asks = sorted(self.asks.items(), key=lambda kv: float(kv[0]))[:25]
        parts = []
        for i in range(25):
            if i < len(bids):
                parts += [bids[i][0], bids[i][1]]
            if i < len(asks):
                parts += [asks[i][0], asks[i][1]]
        c = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
        return c - 2**32 if c >= 2**31 else c


class BookStream:
    def __init__(self, ex, mk):
        self.ex, self.mk = ex, mk
        self.name = f"{ex}/{mk}"
        factor = 0.01 if (ex == "okx" and mk == "swap") else 1.0
        self.book = Book(factor)
        self.writer = HourWriter(os.path.join(DATA_DIR, ex, mk))
        self.stats = {"resyncs": 0, "checksum_failures": 0, "seq_breaks": 0,
                      "updates": 0, "snapshots": 0, "snapshot_bytes": 0}

    def snapshot_line(self):
        return json.dumps({
            "ts_utc": now_ms(), "exchange": self.ex, "market_type": self.mk,
            "symbol": "BTC-USDT",
            "bids": self.book.top("b", SNAP_LEVELS),
            "asks": self.book.top("a", SNAP_LEVELS)}, separators=(",", ":"))

    async def snapshot_loop(self):
        await asyncio.sleep(1 + (hash(self.name) % 4))  # stagger the four books
        while True:
            await asyncio.sleep(SNAP_EVERY)
            if self.book.synced and not disk_suspended and (self.book.bids or self.book.asks):
                line = self.snapshot_line()
                self.writer.write_line(line, now_ms())
                self.stats["snapshots"] += 1
                self.stats["snapshot_bytes"] += len(line)

    # ---------- Binance ----------
    async def run_binance(self):
        ws_url = ("wss://data-stream.binance.vision/ws/btcusdt@depth@100ms"
                  if self.mk == "spot" else
                  "wss://fstream.binance.com/ws/btcusdt@depth@100ms")
        snap_url = ("https://data-api.binance.vision/api/v3/depth" if self.mk == "spot"
                    else "https://fapi.binance.com/fapi/v1/depth")
        while True:
            self.book.synced = False
            try:
                async with ws_connect(ws_url, ping_interval=20) as ws:
                    queue = asyncio.Queue()

                    async def receiver():
                        async for msg in ws:
                            await queue.put(json.loads(msg))

                    recv_task = asyncio.create_task(receiver())
                    try:
                        snap = await asyncio.to_thread(
                            http_json, snap_url, {"symbol": "BTCUSDT", "limit": "1000"})
                        S = int(snap["lastUpdateId"])
                        self.book.load_snapshot(
                            [(p, q) for p, q in snap["bids"]],
                            [(p, q) for p, q in snap["asks"]])
                        prev_u, started = None, False
                        log(f"{self.name} snapshot loaded (lastUpdateId={S}); syncing")
                        while True:
                            ev = await queue.get()
                            U, u = int(ev["U"]), int(ev["u"])
                            if not started:
                                if u <= S:
                                    continue
                                if self.mk == "swap":
                                    if int(ev["pu"]) > S:
                                        raise RuntimeError(
                                            f"gap before first event: pu={ev['pu']} S={S}")
                                elif not (U <= S + 1 <= u):
                                    raise RuntimeError(
                                        f"first event does not straddle snapshot: U={U} u={u} S={S}")
                                started, prev_u = True, u
                            else:
                                if self.mk == "swap":
                                    if int(ev["pu"]) != prev_u:
                                        self.stats["seq_breaks"] += 1
                                        raise RuntimeError(
                                            f"pu chain break: pu={ev['pu']} prev_u={prev_u}")
                                elif U != prev_u + 1:
                                    self.stats["seq_breaks"] += 1
                                    raise RuntimeError(f"U break: U={U} prev_u={prev_u}")
                                prev_u = u
                            self.book.apply_side("b", [(p, q) for p, q in ev["b"]])
                            self.book.apply_side("a", [(p, q) for p, q in ev["a"]])
                            self.stats["updates"] += 1
                    finally:
                        recv_task.cancel()
            except Exception as e:
                self.stats["resyncs"] += 1
                log(f"{self.name} book resync ({e})")
                await asyncio.sleep(3)

    # ---------- OKX ----------
    async def run_okx(self):
        inst = "BTC-USDT" if self.mk == "spot" else "BTC-USDT-SWAP"
        while True:
            self.book.synced = False
            try:
                async with ws_connect("wss://ws.okx.com:443/ws/v5/public",
                                      ping_interval=None) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": [
                        {"channel": "books", "instId": inst}]}))
                    last_ping = time.time()
                    last_seq = None
                    async for msg in ws:
                        if msg == "pong":
                            continue
                        if time.time() - last_ping > 20:
                            await ws.send("ping")
                            last_ping = time.time()
                        d = json.loads(msg)
                        if "data" not in d:
                            continue
                        item = d["data"][0]
                        bids = [(x[0], x[1]) for x in item["bids"]]
                        asks = [(x[0], x[1]) for x in item["asks"]]
                        if d.get("action") == "snapshot":
                            self.book.load_snapshot(bids, asks)
                            last_seq = item.get("seqId")
                            log(f"{self.name} book snapshot loaded "
                                f"({len(bids)}x{len(asks)} levels)")
                        else:
                            if last_seq is not None and item.get("prevSeqId") != last_seq:
                                self.stats["seq_breaks"] += 1
                                raise RuntimeError(
                                    f"seq chain break: prevSeqId={item.get('prevSeqId')} "
                                    f"last_seq={last_seq}")
                            last_seq = item.get("seqId")
                            self.book.apply_side("b", bids)
                            self.book.apply_side("a", asks)
                        self.stats["updates"] += 1
                        cs = item.get("checksum")
                        if cs and int(cs) != 0 and self.book.okx_checksum() != int(cs):
                            self.stats["checksum_failures"] += 1
                            raise RuntimeError(
                                f"checksum mismatch local={self.book.okx_checksum()} remote={cs}")
            except Exception as e:
                self.stats["resyncs"] += 1
                log(f"{self.name} book resync ({e})")
                await asyncio.sleep(3)

    async def run(self):
        runner = self.run_okx() if self.ex == "okx" else self.run_binance()
        await asyncio.gather(runner, self.snapshot_loop())


async def stats_loop(streams):
    t0 = time.time()
    while True:
        await asyncio.sleep(60)
        snap = {"ts": now_ms(), "uptime_s": int(time.time() - t0),
                "disk_suspended": disk_suspended,
                "free_gib": round(shutil.disk_usage("/").free / 1024**3, 2),
                "streams": {s.name: {**s.stats, "synced": s.book.synced,
                                     "levels": [len(s.book.bids), len(s.book.asks)]}
                            for s in streams}}
        tmp = STATS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, STATS_PATH)


async def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    streams = [BookStream(ex, mk) for ex in ("okx", "binance") for mk in ("spot", "swap")]
    log("books service start: 4 books, snapshot every 5s, top 100")
    await asyncio.gather(disk_guard(), stats_loop(streams),
                         *[s.run() for s in streams])


if __name__ == "__main__":
    asyncio.run(main())
