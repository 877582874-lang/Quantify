#!/usr/bin/env python3
"""Quant system v2 - self-computed BTC/USD TWAP-60s (Chainlink-style).

Purpose: a settlement-grade reference for the Gate event-contract
simulation. Chainlink's official BTC/USD TWAP-60s Data Stream is a
credentialed premium product (feed id masked on the public page), so
this service computes the same statistic from our own tick feeds:

  TWAP60(t) = (1/60s) * integral of price over [t-60s, t]

Method, chosen to minimise error and latency:
- Price input = actual trade prints (not book mids), event-driven:
  every trade from 4 venues (OKX + Binance x spot + USDT swap)
  updates a per-venue price path the moment it arrives.
- The integral is EXACT for the piecewise-constant (zero-order-hold)
  price path - no sampling grid, no approximation beyond the path
  itself. Recomputed every 1 s.
- Composite = MEDIAN of the venue TWAPs (the same robust aggregation
  style Chainlink uses across its sources); a venue is used only if
  its path covers >= 55 s of the window and its last trade is < 10 s
  old. The venue spread (max-min, bps) is published alongside as a
  live dispersion gauge.
- Honest limit: Chainlink's venue set/weights are theirs; this is
  the closest reproducible approximation, not their number.

Output (for the magacquant web page):
  /var/www/magacquant/twap.json         - current value, 1 s cadence
  /var/www/magacquant/twap-history.json - 5 s samples, trailing 2 h
"""
import asyncio, json, os, time
from collections import deque

for k in list(os.environ):
    if "proxy" in k.lower():
        del os.environ[k]

WEB_DIR = "/var/www/magacquant"
WINDOW_MS = 60_000
MIN_COVERAGE_MS = 55_000
STALE_MS = 10_000
CTVAL = 0.01

from websockets.asyncio.client import connect as ws_connect


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def now_ms():
    return int(time.time() * 1000)


class Venue:
    def __init__(self, name):
        self.name = name
        self.events = deque()      # (ts_ms, price) per trade, in order
        self.first_ts = None       # first event ever (coverage base)
        self.last_recv = 0

    def add(self, ts, price):
        if self.events and ts < self.events[-1][0]:
            ts = self.events[-1][0]  # keep the path non-decreasing
        self.events.append((ts, price))
        if self.first_ts is None:
            self.first_ts = ts
        self.last_recv = now_ms()

    def twap(self, t):
        """(twap, coverage_ms) over [t-WINDOW, t] with zero-order hold,
        or (None, coverage) if the path does not reach the window start."""
        start = t - WINDOW_MS
        ev = self.events
        while len(ev) > 1 and ev[1][0] <= start:
            ev.popleft()
        if not ev or ev[0][0] > start:
            cov = 0 if self.first_ts is None else max(0, t - max(self.first_ts, start))
            return None, cov
        area, prev_ts, prev_px = 0.0, start, ev[0][1]
        for ts, px in ev:
            if ts <= start:
                prev_px = px
                continue
            if ts > t:
                break
            area += prev_px * (ts - prev_ts)
            prev_ts, prev_px = ts, px
        area += prev_px * (t - prev_ts)
        return area / WINDOW_MS, WINDOW_MS


VENUES = {f"{ex}/{mk}": Venue(f"{ex}/{mk}")
          for ex in ("okx", "binance") for mk in ("spot", "swap")}


async def run_okx(mk):
    v = VENUES[f"okx/{mk}"]
    inst = "BTC-USDT" if mk == "spot" else "BTC-USDT-SWAP"
    while True:
        try:
            async with ws_connect("wss://ws.okx.com:443/ws/v5/public",
                                  ping_interval=None) as ws:
                await ws.send(json.dumps({"op": "subscribe", "args": [
                    {"channel": "trades", "instId": inst}]}))
                log(f"okx/{mk} connected")
                last_ping = time.time()
                async for msg in ws:
                    if msg == "pong":
                        continue
                    if time.time() - last_ping > 20:
                        await ws.send("ping")
                        last_ping = time.time()
                    for r in json.loads(msg).get("data", []):
                        v.add(int(r["ts"]), float(r["px"]))
        except Exception as e:
            log(f"okx/{mk} WS error: {e} -> reconnect in 3s")
            await asyncio.sleep(3)


async def run_binance(mk):
    v = VENUES[f"binance/{mk}"]
    host = ("wss://data-stream.binance.vision/ws/btcusdt@trade" if mk == "spot"
            else "wss://fstream.binance.com/ws/btcusdt@trade")
    while True:
        try:
            async with ws_connect(host, ping_interval=20) as ws:
                log(f"binance/{mk} connected")
                async for msg in ws:
                    d = json.loads(msg)
                    if d.get("e") == "trade":
                        v.add(int(d["T"]), float(d["p"]))
        except Exception as e:
            log(f"binance/{mk} WS error: {e} -> reconnect in 3s")
            await asyncio.sleep(3)


def atomic_write(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)


async def publish_loop():
    history = deque(maxlen=1440)  # 5 s samples x 2 h
    last_hist = 0
    while True:
        await asyncio.sleep(1)
        t = now_ms()
        sources, usable = {}, []
        for name, v in VENUES.items():
            tw, cov = v.twap(t)
            fresh = t - v.last_recv < STALE_MS if v.last_recv else False
            sources[name] = {
                "twap": round(tw, 2) if tw is not None else None,
                "last": v.events[-1][1] if v.events else None,
                "coverage_s": round(cov / 1000, 1),
                "fresh": fresh}
            if tw is not None and cov >= MIN_COVERAGE_MS and fresh:
                usable.append(tw)
        if len(usable) >= 2:
            usable.sort()
            n = len(usable)
            comp = usable[n // 2] if n % 2 else (usable[n // 2 - 1] + usable[n // 2]) / 2
            spread_bps = (usable[-1] - usable[0]) / comp * 10_000
            warming = False
        else:
            comp, spread_bps, warming = None, None, True
        payload = {"ts": t, "twap": round(comp, 2) if comp is not None else None,
                   "window_s": 60, "warming_up": warming,
                   "spread_bps": round(spread_bps, 2) if spread_bps is not None else None,
                   "sources": sources}
        try:
            atomic_write(os.path.join(WEB_DIR, "twap.json"), payload)
            if comp is not None and t - last_hist >= 5000:
                history.append({"ts": t, "twap": payload["twap"]})
                atomic_write(os.path.join(WEB_DIR, "twap-history.json"),
                             list(history))
                last_hist = t
        except Exception as e:
            log(f"publish failed: {e}")


async def main():
    os.makedirs(WEB_DIR, exist_ok=True)
    log("twap service start: 4 venues, exact-integral TWAP-60s, median composite")
    await asyncio.gather(publish_loop(),
                         run_okx("spot"), run_okx("swap"),
                         run_binance("spot"), run_binance("swap"))


if __name__ == "__main__":
    asyncio.run(main())
