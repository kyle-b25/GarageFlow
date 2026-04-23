"""
garage_sim.py — GarageFlow Traffic Simulator
============================================
Simulates realistic car arrivals and departures against a live GarageFlow
API instance.  Displays a live dashboard in the terminal.

Usage:
    python garage_sim.py

All tunables are in the CONFIG block immediately below the imports.
"""

import random
import string
import sys
import time
import threading
import os
from datetime import datetime

try:
    import requests
except ImportError:
    print("'requests' is not installed.  Run:  pip install requests")
    sys.exit(1)


# ═════════════════════════════════════════════════════════════════════════════
#  CONFIG  —  Every tunable value lives here.  Edit freely.
# ═════════════════════════════════════════════════════════════════════════════

# ── Server ────────────────────────────────────────────────────────────────────

# URL of the running Flask / GarageFlow server.
# Shown as the default when the program prompts you at startup.
DEFAULT_SERVER_URL = "http://127.0.0.1:5000"

# ── Simulation defaults ───────────────────────────────────────────────────────

# Default simulation length shown at the prompt (accepts "2h", "30m", "90s",
# or a plain number of seconds).
DEFAULT_DURATION = "30m"

# Default average number of car arrivals per minute shown at the prompt.
# Actual inter-arrival gaps follow a Poisson (exponential) distribution so
# traffic is bursty rather than perfectly metered.
DEFAULT_ARRIVALS_PER_MINUTE = 1.0 #was 3.0 changed to 1

# ── Vehicle behaviour ─────────────────────────────────────────────────────────

# How long a car stays in the garage, in minutes.
# Each car gets a random duration uniformly sampled between these two values.
MIN_STAY_MINUTES = 2
MAX_STAY_MINUTES = 10   #was 90 edited to 10

# Driver-class distribution.
# Example below: standard appears 3x so it is picked ~50% of the time.
#DRIVER_CLASS_POOL = [
#    "standard",
#    "standard",
#    "standard",
#    "eco",
#    "accessibility",
#    "employee",
#]
DRIVER_CLASS_POOL = ["standard"]


# Payment methods randomly assigned when a car exits.
# Valid values: "cash", "card", "mobile"
PAYMENT_METHOD_POOL = ["cash", "card", "mobile"]

# ── License-plate generation ──────────────────────────────────────────────────

# Number of random uppercase letters at the start of a generated plate.
PLATE_LETTER_COUNT = 3

# Number of random digits at the end of a generated plate.
PLATE_DIGIT_COUNT = 4

# ── Timing & polling ──────────────────────────────────────────────────────────

# How often the dashboard redraws, in seconds.
DISPLAY_REFRESH_SECONDS = 1.0

# How often the exit worker checks whether any parked car's time is up,
# in seconds.  Lower = more responsive exits; higher = less CPU churn.
EXIT_CHECK_INTERVAL_SECONDS = 1.0

# How often the floor poller refreshes the available-spot count from the API,
# in seconds.
FLOOR_POLL_INTERVAL_SECONDS = 5.0

# How long to wait for any single HTTP request before giving up, in seconds.
REQUEST_TIMEOUT_SECONDS = 8

# Brief pause after you confirm the prompts, before the simulation starts,
# in seconds.  Gives you a moment to read your settings.
STARTUP_DELAY_SECONDS = 1

# How often the main thread checks whether the simulation window has ended,
# in seconds.  Has no effect on display or API frequency.
MAIN_LOOP_SLEEP_SECONDS = 0.5

# How long (in seconds) to wait for each worker thread to finish cleanly
# when the simulation ends or is interrupted.
THREAD_JOIN_TIMEOUT_SECONDS = 5

# ── Error log ─────────────────────────────────────────────────────────────────

# Maximum number of notice/error messages stored internally at any one time.
MAX_STORED_ERRORS = 6

# How many of those stored messages to show on the live dashboard.
DASHBOARD_ERROR_LINES = 4

# ── Dashboard appearance ──────────────────────────────────────────────────────

# Total inner width of the dashboard box in characters.
DASHBOARD_WIDTH = 60

# Width of the simulation-progress bar in characters.
PROGRESS_BAR_WIDTH = 40

# Width of the garage-occupancy bar in characters.
OCCUPANCY_BAR_WIDTH = 36

# ═════════════════════════════════════════════════════════════════════════════
#  END OF CONFIG  —  No need to edit below this line for normal use.
# ═════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_duration(raw: str) -> float:
    """Parse strings like '2h', '30m', '90s', or plain integers into seconds."""
    raw = raw.strip().lower()
    if raw.endswith('h'):
        return float(raw[:-1]) * 3600
    if raw.endswith('m'):
        return float(raw[:-1]) * 60
    if raw.endswith('s'):
        return float(raw[:-1])
    return float(raw)


def _random_plate() -> str:
    letters = ''.join(random.choices(string.ascii_uppercase, k=PLATE_LETTER_COUNT))
    digits  = ''.join(random.choices(string.digits,          k=PLATE_DIGIT_COUNT))
    return f"{letters}{digits}"


def _clear() -> None:
    os.system('cls' if os.name == 'nt' else 'clear')


def _bar(value: int, maximum: int, width: int) -> str:
    if maximum == 0:
        filled = 0
    else:
        filled = int(width * min(value, maximum) / maximum)
    return '█' * filled + '░' * (width - filled)


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED STATE  (thread-safe via a lock)
# ─────────────────────────────────────────────────────────────────────────────

lock = threading.Lock()

stats = {
    "inside":        0,
    "exited":        0,
    "total_arrived": 0,
    "rejected":      0,
    "revenue":       0.0,
    "available":     "?",
    "total_spots":   "?",
    "errors":        [],
}

# plate -> {ticketId, plate, stay_until}
active_cars: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
#  API CALLS
# ─────────────────────────────────────────────────────────────────────────────

def api_enter(base_url: str, plate: str, driver_class: str) -> dict | None:
    """POST /v1/tickets — returns ticket dict or error dict."""
    try:
        r = requests.post(
            f"{base_url}/v1/tickets",
            json={"licensePlate": plate, "driverClass": driver_class},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if r.status_code == 201:
            return r.json()
        return {"_error": r.json().get("error", f"HTTP {r.status_code}")}
    except Exception as exc:
        return {"_error": str(exc)}


def api_exit(base_url: str, ticket_id: int, plate: str) -> dict | None:
    """PUT /v1/tickets/{id}/exit — returns result dict or error dict."""
    method = random.choice(PAYMENT_METHOD_POOL)
    try:
        r = requests.put(
            f"{base_url}/v1/tickets/{ticket_id}/exit",
            json={"licensePlate": plate, "paymentMethod": method},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if r.status_code == 200:
            return r.json()
        return {"_error": r.json().get("error", f"HTTP {r.status_code}")}
    except Exception as exc:
        return {"_error": str(exc)}


def api_floors(base_url: str) -> tuple[int, int]:
    """GET /v1/floors — returns (available, total) or (-1, -1) on error."""
    try:
        r = requests.get(f"{base_url}/v1/floors", timeout=REQUEST_TIMEOUT_SECONDS)
        if r.status_code == 200:
            floors = r.json()
            avail = sum(f.get("availableSpots", 0) for f in floors)
            total = sum(f.get("totalSpots", 0)     for f in floors)
            return avail, total
    except Exception:
        pass
    return -1, -1


# ─────────────────────────────────────────────────────────────────────────────
#  WORKER THREADS
# ─────────────────────────────────────────────────────────────────────────────

def _log_notice(msg: str) -> None:
    """Append a timestamped notice (caller must already hold the lock)."""
    ts = datetime.now().strftime("%H:%M:%S")
    stats["errors"].append(f"[{ts}] {msg}")
    if len(stats["errors"]) > MAX_STORED_ERRORS:
        stats["errors"].pop(0)


def arrival_worker(base_url: str, rate_per_minute: float,
                   end_time: float, stop_event: threading.Event) -> None:
    """Fires car arrivals at a Poisson-distributed rate until end_time."""
    avg_interval = 60.0 / rate_per_minute   # seconds between arrivals on average

    while not stop_event.is_set() and time.time() < end_time:
        # Exponential inter-arrival gap gives a true Poisson process
        wait = random.expovariate(1.0 / avg_interval)
        stop_event.wait(timeout=wait)
        if stop_event.is_set() or time.time() >= end_time:
            break

        plate        = _random_plate()
        driver_class = random.choice(DRIVER_CLASS_POOL)
        stay_sec     = random.uniform(MIN_STAY_MINUTES * 60, MAX_STAY_MINUTES * 60)

        with lock:
            stats["total_arrived"] += 1

        result = api_enter(base_url, plate, driver_class)

        if result and "_error" not in result:
            stay_until = time.time() + stay_sec
            with lock:
                stats["inside"] += 1
                active_cars[plate] = {
                    "ticketId":   result["ticketId"],
                    "plate":      plate,
                    "stay_until": stay_until,
                }
        else:
            err = result.get("_error", "unknown") if result else "no response"
            with lock:
                stats["rejected"] += 1
                # Suppress the expected "garage full" notice to keep the log clean
                if "garage_full" not in err and "full" not in err.lower():
                    _log_notice(f"Entry failed ({plate}): {err}")


def exit_worker(base_url: str, stop_event: threading.Event) -> None:
    """Checks active_cars on a fixed interval; exits any car past its stay_until."""
    while not stop_event.is_set():
        now     = time.time()
        to_exit = []

        with lock:
            for plate, info in list(active_cars.items()):
                if now >= info["stay_until"]:
                    to_exit.append(info.copy())

        for info in to_exit:
            result = api_exit(base_url, info["ticketId"], info["plate"])

            with lock:
                active_cars.pop(info["plate"], None)

                if result and "_error" not in result:
                    stats["inside"]  = max(0, stats["inside"] - 1)
                    stats["exited"] += 1
                    fee = result.get("totalFee", 0) or 0
                    stats["revenue"] += fee
                else:
                    stats["inside"] = max(0, stats["inside"] - 1)
                    err = result.get("_error", "?") if result else "no response"
                    _log_notice(f"Exit failed (#{info['ticketId']}): {err}")

        stop_event.wait(timeout=EXIT_CHECK_INTERVAL_SECONDS)


def floor_poller(base_url: str, stop_event: threading.Event) -> None:
    """Refreshes the available-spot count from /v1/floors on a fixed interval."""
    while not stop_event.is_set():
        avail, total = api_floors(base_url)
        with lock:
            if avail >= 0:
                stats["available"]   = avail
                stats["total_spots"] = total
        stop_event.wait(timeout=FLOOR_POLL_INTERVAL_SECONDS)


# ─────────────────────────────────────────────────────────────────────────────
#  DISPLAY
# ─────────────────────────────────────────────────────────────────────────────

def display_loop(end_time: float, stop_event: threading.Event,
                 duration_sec: float) -> None:
    """Redraws the terminal dashboard on every DISPLAY_REFRESH_SECONDS tick."""
    start_time = end_time - duration_sec
    W = DASHBOARD_WIDTH

    while not stop_event.is_set():
        with lock:
            s        = stats.copy()
            n_active = len(active_cars)
            errors   = list(s["errors"])

        remaining   = max(0, end_time - time.time())
        elapsed     = time.time() - start_time
        pct_done    = elapsed / duration_sec * 100 if duration_sec else 100

        avail   = s["available"]
        total   = s["total_spots"]
        occ_pct = 0
        if isinstance(total, int) and total > 0 and isinstance(avail, int):
            occ_pct = int((total - avail) / total * 100)

        h, rem      = divmod(int(remaining), 3600)
        m, sec      = divmod(rem, 60)
        time_str    = f"{h:02d}:{m:02d}:{sec:02d}"

        eh, erem    = divmod(int(elapsed), 3600)
        em, esec    = divmod(erem, 60)
        elapsed_str = f"{eh:02d}:{em:02d}:{esec:02d}"

        _clear()

        print("╔" + "═" * W + "╗")
        print(f"║{'  🚗  GARAGEFLOW TRAFFIC SIMULATOR':^{W}}║")
        print("╠" + "═" * W + "╣")

        prog_bar = _bar(int(pct_done), 100, PROGRESS_BAR_WIDTH)
        print(f"║  Elapsed : {elapsed_str}  Remaining : {time_str:<10}       ║")
        print(f"║  [{prog_bar}] {pct_done:5.1f}%  ║")
        print("╠" + "═" * W + "╣")

        if isinstance(avail, int) and isinstance(total, int) and total > 0:
            occ_bar = _bar(total - avail, total, OCCUPANCY_BAR_WIDTH)
            print(f"║  GARAGE   [{occ_bar}] {occ_pct:3d}%  ║")
            print(f"║  Available: {avail:<5}  Occupied: {total - avail:<5}  Total: {total:<5}     ║")
        else:
            print(f"║  GARAGE   Available: {str(avail):<6}  Total: {str(total):<6}              ║")
        print("╠" + "═" * W + "╣")

        print(f"║  {'METRIC':<30}{'VALUE':>26}  ║")
        print(f"║  {'─' * 56}  ║")
        print(f"║  {'Cars currently inside':.<30}{'':>2}{n_active:>24}  ║")
        print(f"║  {'Cars that have left':.<30}{'':>2}{s['exited']:>24}  ║")
        print(f"║  {'Total vehicles arrived':.<30}{'':>2}{s['total_arrived']:>24}  ║")
        print(f"║  {'Rejected (garage full)':.<30}{'':>2}{s['rejected']:>24}  ║")
        print(f"║  {'Revenue collected':.<30}{'':>2}{'${:.2f}'.format(s['revenue']):>24}  ║")
        rej_pct = (s['rejected'] / s['total_arrived'] * 100
                   if s['total_arrived'] > 0 else 0.0)
        print(f"║  {'Rejection rate':.<30}{'':>2}{rej_pct:>23.1f}%  ║")
        print("╠" + "═" * W + "╣")

        print(f"║  {'RECENT NOTICES':<{W-2}}  ║")
        recent = errors[-DASHBOARD_ERROR_LINES:]
        if recent:
            for e in recent:
                truncated = e[:W-4]
                print(f"║  {truncated:<{W-2}}  ║")
        else:
            print(f"║  {'(none)':^{W-2}}  ║")

        print("╚" + "═" * W + "╝")
        print("  Press Ctrl+C to stop early.")

        time.sleep(DISPLAY_REFRESH_SECONDS)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  GarageFlow Traffic Simulator")
    print("=" * 60)

    base_url = input(
        f"\nFlask server URL [{DEFAULT_SERVER_URL}]: "
    ).strip() or DEFAULT_SERVER_URL
    base_url = base_url.rstrip("/")

    raw_duration = input(
        f"Simulation duration (e.g. 2h, 30m, 90s) [{DEFAULT_DURATION}]: "
    ).strip() or DEFAULT_DURATION

    try:
        duration_sec = _parse_duration(raw_duration)
    except ValueError:
        fallback = _parse_duration(DEFAULT_DURATION)
        print(f"Could not parse duration.  Using {DEFAULT_DURATION}.")
        duration_sec = fallback

    raw_rate = input(
        f"Average car arrivals per minute [{DEFAULT_ARRIVALS_PER_MINUTE}]: "
    ).strip() or str(DEFAULT_ARRIVALS_PER_MINUTE)

    try:
        rate_per_minute = float(raw_rate)
        if rate_per_minute <= 0:
            raise ValueError
    except ValueError:
        print(f"Invalid rate.  Using {DEFAULT_ARRIVALS_PER_MINUTE} cars/min.")
        rate_per_minute = DEFAULT_ARRIVALS_PER_MINUTE

    print(f"\nStarting {raw_duration} simulation at {rate_per_minute} cars/min …\n")
    time.sleep(STARTUP_DELAY_SECONDS)

    end_time   = time.time() + duration_sec
    stop_event = threading.Event()

    threads = [
        threading.Thread(target=arrival_worker,
                         args=(base_url, rate_per_minute, end_time, stop_event),
                         daemon=True, name="arrivals"),
        threading.Thread(target=exit_worker,
                         args=(base_url, stop_event),
                         daemon=True, name="exits"),
        threading.Thread(target=floor_poller,
                         args=(base_url, stop_event),
                         daemon=True, name="floors"),
        threading.Thread(target=display_loop,
                         args=(end_time, stop_event, duration_sec),
                         daemon=True, name="display"),
    ]

    for t in threads:
        t.start()

    try:
        while time.time() < end_time:
            time.sleep(MAIN_LOOP_SLEEP_SECONDS)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    stop_event.set()
    for t in threads:
        t.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)

    # ── Final report ────────────────────────────────────────────
    _clear()
    with lock:
        s        = stats.copy()
        n_active = len(active_cars)

    rej_pct = (s['rejected'] / s['total_arrived'] * 100
               if s['total_arrived'] > 0 else 0.0)
    avg_fee = s['revenue'] / s['exited'] if s['exited'] > 0 else 0.0

    print("\n" + "=" * 55)
    print("  SIMULATION COMPLETE — FINAL REPORT")
    print("=" * 55)
    print(f"  Duration requested    : {raw_duration}")
    print(f"  Arrivals per minute   : {rate_per_minute}")
    print("-" * 55)
    print(f"  Cars still inside     : {n_active}")
    print(f"  Cars that exited      : {s['exited']}")
    print(f"  Total vehicles arrived: {s['total_arrived']}")
    print(f"  Rejected (garage full): {s['rejected']}  ({rej_pct:.1f}%)")
    print(f"  Revenue collected     : ${s['revenue']:.2f}")
    print(f"  Average fee per exit  : ${avg_fee:.2f}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
