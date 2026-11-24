"""OKX public REST adapter (B1). Public market data only; no credentials, no trading endpoints."""
import time
import random
import requests

BASE = "https://www.okx.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BAR_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}


class OkxError(Exception):
    pass


class OkxClient:
    def __init__(self, min_interval_sec=0.35, max_retries=5, timeout=15):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.min_interval_sec = min_interval_sec
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_req_at = 0.0
        self.request_count = 0
        self.failure_count = 0

    def _throttle(self):
        dt = time.time() - self._last_req_at
        if dt < self.min_interval_sec:
            time.sleep(self.min_interval_sec - dt + random.uniform(0, 0.05))

    def history_candles_page(self, inst_id, interval, after_ms=None, limit=100):
        """One page of /api/v5/market/history-candles. Returns list of raw arrays (newest first)."""
        params = {"instId": inst_id, "bar": BAR_MAP[interval], "limit": str(limit)}
        if after_ms is not None:
            params["after"] = str(after_ms)
        backoff = 1.0
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            started = time.time()
            try:
                self.request_count += 1
                resp = self.session.get(BASE + "/api/v5/market/history-candles",
                                        params=params, timeout=self.timeout)
                self._last_req_at = time.time()
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise OkxError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("code") != "0":
                    raise OkxError(f"OKX code={payload.get('code')} msg={payload.get('msg')}")
                return payload.get("data", []), {"started_at": started, "ended_at": time.time(),
                                                "attempts": attempt, "http_status": resp.status_code}
            except Exception as e:  # network or API error: retry with backoff, never loop forever
                last_err = e
                self.failure_count += 1
                self._last_req_at = time.time()
                if attempt < self.max_retries:
                    time.sleep(backoff + random.uniform(0, 0.3))
                    backoff = min(backoff * 2, 30.0)
        raise OkxError(f"history-candles failed after {self.max_retries} attempts: {last_err}")
