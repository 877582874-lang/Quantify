#!/usr/bin/env python3
"""Quant system v2 - disk janitor (MONITOR ONLY - deletes nothing).

Runs daily via quant-janitor.timer. Measures the data trees, keeps a
size history (logs/disk-history.json), estimates the archive growth
rate, and projects how many days remain until the 8 GiB free-space
floor. Logs DISK_REPORT every run; logs ALARM_DISK_PROJECTION when
the projection drops under 30 days. Retention/deletion policy is
Cl's decision - this script never removes a byte.
"""
import json, os, time

BASE = "/opt/quant-system"
HISTORY_PATH = os.path.join(BASE, "logs", "disk-history.json")
TREES = ("data/trades", "data/books", "data/quarantine")
FLOOR = 8 * 1024**3


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def tree_bytes(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def main():
    sizes = {t: tree_bytes(os.path.join(BASE, t)) for t in TREES}
    total = sum(sizes.values())
    st = os.statvfs("/")
    free = st.f_bavail * st.f_frsize
    hist = []
    if os.path.exists(HISTORY_PATH):
        try:
            hist = json.load(open(HISTORY_PATH))
        except Exception:
            hist = []
    hist.append({"ts": int(time.time()), "total": total, "free": free})
    hist = hist[-400:]
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(hist, f)
    os.replace(tmp, HISTORY_PATH)
    rate = None
    for old in hist:
        age = hist[-1]["ts"] - old["ts"]
        if age >= 20 * 3600:
            rate = (total - old["total"]) / (age / 86400)  # bytes per day
            break
    parts = " ".join(f"{t}={sizes[t]/1024**2:.0f}MB" for t in TREES)
    msg = (f"DISK_REPORT {parts} total={total/1024**2:.0f}MB "
           f"free={free/1024**3:.2f}GiB")
    if rate and rate > 0:
        days = (free - FLOOR) / rate
        msg += f" growth={rate/1024**2:.0f}MB/day days_to_floor={days:.0f}"
        log(msg)
        if days < 30:
            log(f"ALARM_DISK_PROJECTION only {days:.0f} days to the 8 GiB floor "
                f"at the current growth rate - retention decision needed")
    else:
        log(msg + " growth=n/a (history < 20h)")


if __name__ == "__main__":
    main()
