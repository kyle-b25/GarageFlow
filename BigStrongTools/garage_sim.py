"""
garage_sim.py — GarageFlow Traffic Simulator
============================================
Simulates realistic car arrivals, departures, and reservations (with
configurable no-show logic) against a live GarageFlow API instance.
Displays a live dashboard in the terminal.

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
DEFAULT_SERVER_URL = "http://127.0.0.1:5000"

# ── Simulation defaults ───────────────────────────────────────────────────────

# Accepted suffixes: "2h", "30m", "90s", or a plain number of seconds.
DEFAULT_DURATION = "30m"

# Average number of walk-up car arrivals per minute.
DEFAULT_ARRIVALS_PER_MINUTE = 1.0

# Average number of reservation requests created per minute.
# Set to 0 to disable reservations entirely.
DEFAULT_RESERVATIONS_PER_MINUTE = 0.5

# ── Reservation behaviour ─────────────────────────────────────────────────────

# Maximum number of minutes in advance a reservation can be scheduled.
# Actual lead time is sampled uniformly between ~10 seconds and this value.
# Must be ≤ 15 per the reservation validation rules in the API.
MAX_RESERVATION_LEAD_MINUTES = 15

# Probability (0.0–1.0) that a reservation holder will NOT show up.
#   0.0 = everyone arrives on time
#   1.0 = nobody shows up
RESERVATION_NOSHOW_PROBABILITY = 0.25

# Seconds after a reservation's scheduled arrival before the sim officially
# declares it a missed/no-show.  Acts as a realistic "late arrival" window.
RESERVATION_GRACE_PERIOD_SECONDS = 30

# ── Vehicle behaviour ─────────────────────────────────────────────────────────

# Each car stays a random number of minutes sampled between these bounds.
MIN_STAY_MINUTES = 2
MAX_STAY_MINUTES = 10

# Driver-class pool — sampled with replacement for each arrival.
# Duplicate entries increase that class's probability.
# e.g. ["standard","standard","standard","eco","accessibility","employee"]
#      gives 50 % standard and 50 % other.
DRIVER_CLASS_POOL = ["standard"]

# Valid values: "cash", "card", "mobile"
PAYMENT_METHOD_POOL = ["cash", "card", "mobile"]

# ── License-plate / phone generation ─────────────────────────────────────────

# Plate format: <PLATE_LETTER_COUNT letters><PLATE_DIGIT_COUNT digits>
PLATE_LETTER_COUNT = 3
PLATE_DIGIT_COUNT  = 4

# ── Timing & polling ──────────────────────────────────────────────────────────

# How often the live dashboard redraws, in seconds.
DISPLAY_REFRESH_SECONDS = 1.0

# How often the exit worker checks whether any parked car's time is up.
EXIT_CHECK_INTERVAL_SECONDS = 1.0

# How often the reservation check-in worker scans for due arrivals.
CHECKIN_CHECK_INTERVAL_SECONDS = 1.0

# How often the floor poller refreshes the available-spot count from the API.
FLOOR_POLL_INTERVAL_SECONDS = 5.0

# Timeout for any single HTTP request.
REQUEST_TIMEOUT_SECONDS = 8

# Pause before the simulation starts (lets you read the settings summary).
STARTUP_DELAY_SECONDS = 1

# How often the main thread checks whether the simulation window has ended.
MAIN_LOOP_SLEEP_SECONDS = 0.5

# How long to wait for each worker thread to finish when the sim ends.
THREAD_JOIN_TIMEOUT_SECONDS = 5

# ── Error log ─────────────────────────────────────────────────────────────────

# Maximum number of notice / error messages stored internally at any one time.
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
    """Parse strings like '2h', '30m', '90s', or plain numbers into seconds."""
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


def _random_phone() -> str:
    """Generate a plausible US phone number string."""
    area = random.randint(200, 999)
    mid  = random.randint(200, 999)
    end  = random.randint(1000, 9999)
    return f"{area}-{mid}-{end}"


def _clear() -> None:
    os.system('cls' if os.name == 'nt' else 'clear')


def _bar(value: int, maximum: int, width: int) -> str:
    if maximum == 0:
        filled = 0
    else:
        filled = int(width * min(value, maximum) / maximum)
    return '█' * filled + '░' * (width - filled)


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED STATE
# ─────────────────────────────────────────────────────────────────────────────

lock = threading.Lock()

stats = {
    # ── Walk-up traffic ──────────────────────────────────────────────────────
    "inside":          0,      # cars currently parked (walk-up + checked-in res)
    "exited":          0,      # cars that completed a full park-and-exit cycle
    "total_arrived":   0,      # all walk-up arrival attempts
    "rejected":        0,      # walk-up rejections (garage full, API errors)
    "revenue":         0.0,    # total fees collected on exit
    # ── Reservations ─────────────────────────────────────────────────────────
    "res_made":        0,      # reservations successfully POST-ed to the API
    "res_fulfilled":   0,      # reservations that checked in on time
    "res_missed":      0,      # no-shows: grace period expired without check-in
    "res_api_errors":  0,      # API failures on create or check-in (not counted
                               #   as missed — those are infrastructure failures,
                               #   not driver behaviour)
    # ── Garage state (from floor poller) ─────────────────────────────────────
    "available":       "?",
    "total_spots":     "?",
    # ── Log ──────────────────────────────────────────────────────────────────
    "errors":          [],
}

# plate → { ticketId, plate, stay_until }
# Shared by both walk-up arrivals and successfully checked-in reservations.
active_cars: dict = {}

# Reservations that have been made but whose arrival time hasn't been
# reached yet (or whose grace period hasn't expired yet for no-shows).
# Each entry: {
#   "reservationId": str,       e.g. "R-0003"
#   "plate":         str,
#   "phone":         str,
#   "scheduled_ts":  float,     Unix timestamp of scheduledArrival
#   "will_show":     bool,      decided at creation time
# }
pending_reservations: list = []


# ─────────────────────────────────────────────────────────────────────────────
#  API CALLS
# ─────────────────────────────────────────────────────────────────────────────

def api_enter(base_url: str, plate: str, driver_class: str) -> dict | None:
    """POST /v1/tickets — returns ticket dict or {"_error": ...}."""
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
    """PUT /v1/tickets/{id}/exit — returns result dict or {"_error": ...}."""
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


def api_post_reservation(base_url: str, phone: str, plate: str,
                          arrival_iso: str, driver_class: str) -> dict | None:
    """POST /v1/reservations — returns reservation dict or {"_error": ...}."""
    try:
        r = requests.post(
            f"{base_url}/v1/reservations",
            json={
                "phone":            phone,
                "licensePlate":     plate,
                "scheduledArrival": arrival_iso,
                "driverClass":      driver_class,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if r.status_code == 201:
            return r.json()
        return {"_error": r.json().get("error", f"HTTP {r.status_code}")}
    except Exception as exc:
        return {"_error": str(exc)}


def api_checkin_reservation(base_url: str, reservation_id: str,
                             plate: str) -> dict | None:
    """PUT /v1/reservations/{id}/check — returns ticket dict or {"_error": ...}."""
    try:
        r = requests.put(
            f"{base_url}/v1/reservations/{reservation_id}/check",
            json={"licensePlate": plate},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if r.status_code == 201:
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
            total = sum(f.get("totalSpots",     0) for f in floors)
            return avail, total
    except Exception:
        pass
    return -1, -1


# ─────────────────────────────────────────────────────────────────────────────
#  WORKER THREADS
# ─────────────────────────────────────────────────────────────────────────────

def _log_notice(msg: str) -> None:
    """Append a timestamped notice — caller must already hold `lock`."""
    ts = datetime.now().strftime("%H:%M:%S")
    stats["errors"].append(f"[{ts}] {msg}")
    if len(stats["errors"]) > MAX_STORED_ERRORS:
        stats["errors"].pop(0)


# ── Walk-up arrivals ──────────────────────────────────────────────────────────

def arrival_worker(base_url: str, rate_per_minute: float,
                   end_time: float, stop_event: threading.Event) -> None:
    """Fires walk-up car arrivals at a Poisson-distributed rate until end_time."""
    avg_interval = 60.0 / rate_per_minute

    while not stop_event.is_set() and time.time() < end_time:
        # Exponential inter-arrival gap → true Poisson process
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


# ── Reservation creation ──────────────────────────────────────────────────────

def reservation_worker(base_url: str, rate_per_minute: float,
                        end_time: float, stop_event: threading.Event) -> None:
    """
    Creates reservations at a Poisson-distributed rate.

    Each reservation is scheduled between ~10 seconds and
    MAX_RESERVATION_LEAD_MINUTES in the future.  Whether the holder
    will show up is decided at creation time by sampling against
    RESERVATION_NOSHOW_PROBABILITY — the check-in worker acts on
    that decision when the arrival time is reached.
    """
    avg_interval = 60.0 / rate_per_minute

    while not stop_event.is_set() and time.time() < end_time:
        wait = random.expovariate(1.0 / avg_interval)
        stop_event.wait(timeout=wait)
        if stop_event.is_set() or time.time() >= end_time:
            break

        plate        = _random_plate()
        phone        = _random_phone()
        driver_class = random.choice(DRIVER_CLASS_POOL)

        # Decide now whether this person will actually show up
        will_show = random.random() >= RESERVATION_NOSHOW_PROBABILITY

        # Random lead time: 10 s → MAX_RESERVATION_LEAD_MINUTES * 60 s
        now = time.time()
        # Max allowed lead so it DOESN'T exceed simulation end
        max_lead = min(
        MAX_RESERVATION_LEAD_MINUTES * 60,
        max(0, end_time - now - 1)  # buffer so it doesn't hit the boundary
        )
        
        # If there's no time left, skip creating reservations
        if max_lead < 10:
            continue
        lead_sec = random.uniform(10, max_lead)
        scheduled_ts = now + lead_sec

        # Convert to ISO 8601 UTC string for the API
        arrival_dt  = datetime.utcfromtimestamp(scheduled_ts)
        arrival_iso = arrival_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        result = api_post_reservation(base_url, phone, plate, arrival_iso, driver_class)

        with lock:
            if result and "_error" not in result:
                stats["res_made"] += 1
                pending_reservations.append({
                    "reservationId": result["reservationId"],
                    "plate":         plate,
                    "phone":         phone,
                    "scheduled_ts":  scheduled_ts,
                    "will_show":     will_show,
                })
                if not will_show:
                    _log_notice(
                        f"No-show booked: {result['reservationId']} "
                        f"@ {arrival_dt.strftime('%H:%M:%S')}"
                    )
            else:
                err = result.get("_error", "unknown") if result else "no response"
                stats["res_api_errors"] += 1
                _log_notice(f"Res create failed ({plate}): {err}")


# ── Reservation check-in / miss detection ─────────────────────────────────────

def checkin_worker(base_url: str, stop_event: threading.Event) -> None:
    """
    Scans pending_reservations every CHECKIN_CHECK_INTERVAL_SECONDS.

    Decision logic per pending reservation:

      will_show=True  + scheduled_ts reached
          → attempt check-in via API
          → on success: add car to active_cars so exit_worker handles the exit
          → on API error: counted as res_api_errors (not a behavioural miss)

      will_show=False + scheduled_ts + RESERVATION_GRACE_PERIOD_SECONDS reached
          → count as res_missed (the driver never showed up)
          → no API call made — the server's background scheduler will eventually
            mark the reservation expired on its own schedule

    Reservation accuracy guarantee:
      res_made      = every 201 response from POST /v1/reservations
      res_fulfilled = every 201 response from PUT /v1/reservations/{id}/check
      res_missed    = every no-show whose grace window has expired
                      The sim controls scheduled_ts so it holds ground truth;
                      we never need to poll the server for expiry state.
    """
    while not stop_event.is_set():
        now        = time.time()
        to_checkin = []
        to_miss    = []

        with lock:
            remaining = []
            for res in pending_reservations:
                if res["will_show"] and now >= res["scheduled_ts"]:
                    to_checkin.append(res.copy())
                elif (not res["will_show"]
                      and now >= res["scheduled_ts"] + RESERVATION_GRACE_PERIOD_SECONDS):
                    to_miss.append(res.copy())
                else:
                    remaining.append(res)
            pending_reservations[:] = remaining

        # ── Process check-ins (HTTP calls outside the lock) ───────────────
        for res in to_checkin:
            result = api_checkin_reservation(base_url, res["reservationId"], res["plate"])

            with lock:
                if result and "_error" not in result:
                    stats["res_fulfilled"] += 1
                    ticket_id  = result.get("ticketId")
                    stay_sec   = random.uniform(MIN_STAY_MINUTES * 60,
                                               MAX_STAY_MINUTES * 60)
                    # Add to active_cars so the exit_worker handles the
                    # eventual departure and revenue collection.
                    stats["inside"] += 1
                    active_cars[res["plate"]] = {
                        "ticketId":   ticket_id,
                        "plate":      res["plate"],
                        "stay_until": time.time() + stay_sec,
                    }
                    _log_notice(
                        f"Checked in: {res['reservationId']} → ticket #{ticket_id}"
                    )
                else:
                    # Infrastructure failure, not a behavioural no-show
                    err = result.get("_error", "unknown") if result else "no response"
                    stats["res_api_errors"] += 1
                    _log_notice(
                        f"Check-in API error ({res['reservationId']}): {err}"
                    )

        # ── Process confirmed no-shows (pure counting, no API call) ───────
        for res in to_miss:
            with lock:
                stats["res_missed"] += 1
            # (No log entry by default — can be noisy if no-show rate is high)

        stop_event.wait(timeout=CHECKIN_CHECK_INTERVAL_SECONDS)


# ── Exits ─────────────────────────────────────────────────────────────────────

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


# ── Floor availability poller ─────────────────────────────────────────────────

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
                 duration_sec: float, res_enabled: bool) -> None:
    """Redraws the terminal dashboard on every DISPLAY_REFRESH_SECONDS tick."""
    start_time = end_time - duration_sec
    W = DASHBOARD_WIDTH

    while not stop_event.is_set():
        with lock:
            s         = stats.copy()
            n_active  = len(active_cars)
            n_pending = len(pending_reservations)
            errors    = list(s["errors"])

        remaining = max(0, end_time - time.time())
        elapsed   = time.time() - start_time
        pct_done  = elapsed / duration_sec * 100 if duration_sec else 100

        avail   = s["available"]
        total   = s["total_spots"]
        occ_pct = 0
        if isinstance(total, int) and total > 0 and isinstance(avail, int):
            occ_pct = int((total - avail) / total * 100)

        h,  rem  = divmod(int(remaining), 3600)
        m,  sec  = divmod(rem, 60)
        eh, erem = divmod(int(elapsed),   3600)
        em, esec = divmod(erem, 60)
        time_str    = f"{h:02d}:{m:02d}:{sec:02d}"
        elapsed_str = f"{eh:02d}:{em:02d}:{esec:02d}"

        _clear()

        # ── Header ────────────────────────────────────────────────────────
        print("╔" + "═" * W + "╗")
        print(f"║{'  🚗  GARAGEFLOW TRAFFIC SIMULATOR':^{W}}║")
        print("╠" + "═" * W + "╣")

        prog_bar = _bar(int(pct_done), 100, PROGRESS_BAR_WIDTH)
        print(f"║  Elapsed : {elapsed_str}  Remaining : {time_str:<10}       ║")
        print(f"║  [{prog_bar}] {pct_done:5.1f}%  ║")

        # ── Garage occupancy ──────────────────────────────────────────────
        print("╠" + "═" * W + "╣")
        if isinstance(avail, int) and isinstance(total, int) and total > 0:
            occ_bar = _bar(total - avail, total, OCCUPANCY_BAR_WIDTH)
            print(f"║  GARAGE   [{occ_bar}] {occ_pct:3d}%  ║")
            print(f"║  Available: {avail:<5}  Occupied: {total - avail:<5}  Total: {total:<5}     ║")
        else:
            print(f"║  GARAGE   Available: {str(avail):<6}  Total: {str(total):<6}              ║")

        # ── Walk-up traffic ───────────────────────────────────────────────
        print("╠" + "═" * W + "╣")
        print(f"║  {'WALK-UP TRAFFIC':<{W - 2}}  ║")
        print(f"║  {'─' * (W - 4)}  ║")
        print(f"║  {'Cars currently inside':.<30}{'':>2}{n_active:>24}  ║")
        print(f"║  {'Cars that have left':.<30}{'':>2}{s['exited']:>24}  ║")
        print(f"║  {'Total vehicles arrived':.<30}{'':>2}{s['total_arrived']:>24}  ║")
        print(f"║  {'Rejected (garage full)':.<30}{'':>2}{s['rejected']:>24}  ║")
        print(f"║  {'Revenue collected':.<30}{'':>2}{'${:.2f}'.format(s['revenue']):>24}  ║")
        rej_pct = (s['rejected'] / s['total_arrived'] * 100
                   if s['total_arrived'] > 0 else 0.0)
        print(f"║  {'Rejection rate':.<30}{'':>2}{rej_pct:>23.1f}%  ║")

        # ── Reservations ─────────────────────────────────────────────────
        if res_enabled:
            res_total   = s['res_made']
            fulfill_pct = (s['res_fulfilled'] / res_total * 100) if res_total > 0 else 0.0
            miss_pct    = (s['res_missed']    / res_total * 100) if res_total > 0 else 0.0

            print("╠" + "═" * W + "╣")
            print(f"║  {'RESERVATIONS':<{W - 2}}  ║")
            print(f"║  {'─' * (W - 4)}  ║")
            print(f"║  {'Reservations made':.<30}{'':>2}{s['res_made']:>24}  ║")
            print(f"║  {'Reservations pending':.<30}{'':>2}{n_pending:>24}  ║")
            print(f"║  {'Reservations fulfilled':.<30}{'':>2}{s['res_fulfilled']:>24}  ║")
            print(f"║  {'Reservations missed (no-show)':.<30}{'':>2}{s['res_missed']:>24}  ║")
            print(f"║  {'Fulfill rate':.<30}{'':>2}{fulfill_pct:>23.1f}%  ║")
            print(f"║  {'Miss rate':.<30}{'':>2}{miss_pct:>23.1f}%  ║")
            if s['res_api_errors'] > 0:
                print(f"║  {'API errors (create/check-in)':.<30}{'':>2}{s['res_api_errors']:>24}  ║")

        # ── Recent notices ────────────────────────────────────────────────
        print("╠" + "═" * W + "╣")
        print(f"║  {'RECENT NOTICES':<{W - 2}}  ║")
        recent = errors[-DASHBOARD_ERROR_LINES:]
        if recent:
            for e in recent:
                print(f"║  {e[:W - 4]:<{W - 4}}  ║")
        else:
            print(f"║  {'(none)':^{W - 2}}  ║")

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

    # ── Prompts ───────────────────────────────────────────────────────────────
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
        print(f"Could not parse duration.  Using {DEFAULT_DURATION}.")
        duration_sec = _parse_duration(DEFAULT_DURATION)

    raw_rate = input(
        f"Average walk-up arrivals per minute [{DEFAULT_ARRIVALS_PER_MINUTE}]: "
    ).strip() or str(DEFAULT_ARRIVALS_PER_MINUTE)
    try:
        rate_per_minute = float(raw_rate)
        if rate_per_minute <= 0:
            raise ValueError
    except ValueError:
        print(f"Invalid rate.  Using {DEFAULT_ARRIVALS_PER_MINUTE} cars/min.")
        rate_per_minute = DEFAULT_ARRIVALS_PER_MINUTE

    raw_res_rate = input(
        f"Average reservations per minute (0 to disable) [{DEFAULT_RESERVATIONS_PER_MINUTE}]: "
    ).strip() or str(DEFAULT_RESERVATIONS_PER_MINUTE)
    try:
        res_rate = float(raw_res_rate)
        if res_rate < 0:
            raise ValueError
    except ValueError:
        print(f"Invalid rate.  Using {DEFAULT_RESERVATIONS_PER_MINUTE} res/min.")
        res_rate = DEFAULT_RESERVATIONS_PER_MINUTE

    res_enabled = res_rate > 0

    # ── Settings summary ──────────────────────────────────────────────────────
    print(f"\nStarting {raw_duration} simulation …")
    print(f"  Walk-up arrivals : {rate_per_minute}/min")
    if res_enabled:
        print(
            f"  Reservations     : {res_rate}/min  "
            f"| lead time up to {MAX_RESERVATION_LEAD_MINUTES} min  "
            f"| no-show rate {int(RESERVATION_NOSHOW_PROBABILITY * 100)}%  "
            f"| grace period {RESERVATION_GRACE_PERIOD_SECONDS}s"
        )
    else:
        print("  Reservations     : disabled")
    print()
    time.sleep(STARTUP_DELAY_SECONDS)

    # ── Thread setup ──────────────────────────────────────────────────────────
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
                         args=(end_time, stop_event, duration_sec, res_enabled),
                         daemon=True, name="display"),
    ]

    if res_enabled:
        threads += [
            threading.Thread(target=reservation_worker,
                             args=(base_url, res_rate, end_time, stop_event),
                             daemon=True, name="reservations"),
            threading.Thread(target=checkin_worker,
                             args=(base_url, stop_event),
                             daemon=True, name="checkins"),
        ]

    for t in threads:
        t.start()

    # ── Run ───────────────────────────────────────────────────────────────────
    try:
        while time.time() < end_time:
            time.sleep(MAIN_LOOP_SLEEP_SECONDS)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    stop_event.set()
    for t in threads:
        t.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)

    # ── Final report ──────────────────────────────────────────────────────────
    _clear()
    with lock:
        s         = stats.copy()
        n_active  = len(active_cars)
        n_pending = len(pending_reservations)

    rej_pct = (s['rejected'] / s['total_arrived'] * 100
               if s['total_arrived'] > 0 else 0.0)
    avg_fee = s['revenue'] / s['exited'] if s['exited'] > 0 else 0.0

    print("\n" + "=" * 55)
    print("  SIMULATION COMPLETE — FINAL REPORT")
    print("=" * 55)
    print(f"  Duration requested     : {raw_duration}")
    print(f"  Walk-up rate           : {rate_per_minute}/min")
    print("-" * 55)
    print(f"  Cars still inside      : {n_active}")
    print(f"  Cars that exited       : {s['exited']}")
    print(f"  Total vehicles arrived : {s['total_arrived']}")
    print(f"  Rejected (garage full) : {s['rejected']}  ({rej_pct:.1f}%)")
    print(f"  Revenue collected      : ${s['revenue']:.2f}")
    print(f"  Average fee per exit   : ${avg_fee:.2f}")

    if res_enabled:
        res_total   = s['res_made']
        fulfill_pct = (s['res_fulfilled'] / res_total * 100) if res_total > 0 else 0.0
        miss_pct    = (s['res_missed']    / res_total * 100) if res_total > 0 else 0.0

        print("-" * 55)
        print(f"  Reservation rate       : {res_rate}/min")
        print(f"  No-show probability    : {int(RESERVATION_NOSHOW_PROBABILITY * 100)}%")
        print(f"  Max lead time          : {MAX_RESERVATION_LEAD_MINUTES} min")
        print(f"  Grace period           : {RESERVATION_GRACE_PERIOD_SECONDS}s")
        print("-" * 55)
        print(f"  Reservations made      : {s['res_made']}")
        print(f"  Reservations pending*  : {n_pending}")
        print(f"  Reservations fulfilled : {s['res_fulfilled']}  ({fulfill_pct:.1f}%)")
        print(f"  Reservations missed    : {s['res_missed']}  ({miss_pct:.1f}%)")
        print(f"  API errors             : {s['res_api_errors']}")
        if n_pending > 0:
            print(f"  * {n_pending} reservation(s) had not yet reached their")
            print(f"    scheduled arrival when the simulation ended.")

    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
