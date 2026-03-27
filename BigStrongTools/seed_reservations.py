"""
Sam Gibney 3/27/2026
seed_reservations.py — GarageFlow Reservation Seeder

Run this from your garageflow project root:
    python seed_reservations.py
Or from a BigStrongTools/ subfolder:
    python tests/seed_reservations.py

Features:
  - Optional deletion of all existing reservations (requires typed confirmation)
  - Debug phone numbers (0000000001, 0000000002 …) or random fake numbers
  - Arrival time window: next 24 hours, 7 days, or 30 days
  - Arrival distribution: purely random or rush-hour clustered
  - Status mix: 80% confirmed, 10% expired, 10% cancelled
"""

import sys
import os
import random
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    from app import app, db
    from models import Reservation, ReservationStatusEnum, Floor
except ImportError as e:
    print(f"\n❌  Could not import GarageFlow modules: {e}")
    print("    Make sure you run this script from your garageflow project root.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def divider(char="─", width=56):
    print(char * width)


def header(title):
    divider("═")
    print(f"  {title}")
    divider("═")


def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"  {prompt}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return str(default)
        print("    ⚠  This field is required.")


def ask_int(prompt, min_val=1, max_val=9999, default=None):
    while True:
        raw = ask(prompt, default=default)
        try:
            val = int(raw)
        except ValueError:
            print("    ⚠  Please enter a whole number.")
            continue
        if val < min_val or val > max_val:
            print(f"    ⚠  Must be between {min_val} and {max_val}.")
            continue
        return val


def ask_choice(prompt, choices):
    """Present a numbered menu and return the chosen key."""
    print(f"  {prompt}")
    for i, (key, label) in enumerate(choices, 1):
        print(f"    {i}) {label}")
    while True:
        raw = input("  Choice: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx][0]
        except ValueError:
            pass
        print(f"    ⚠  Please enter a number between 1 and {len(choices)}.")


def ask_yes_no(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("    ⚠  Please enter y or n.")


# ─────────────────────────────────────────────────────────────────────────────
#  Phone number generators
# ─────────────────────────────────────────────────────────────────────────────

def random_phone():
    area = random.randint(200, 999)
    mid  = random.randint(100, 999)
    end  = random.randint(1000, 9999)
    return f"{area}-{mid}-{end}"


def debug_phone(n):
    return str(n).zfill(10)


# ─────────────────────────────────────────────────────────────────────────────
#  Arrival time generators
# ─────────────────────────────────────────────────────────────────────────────

def snap_to_half_hour(dt):
    snap = (dt.minute // 30) * 30
    return dt.replace(minute=snap, second=0, microsecond=0)


def clamp_to_garage_hours(dt):
    if dt.hour < 7:
        return dt.replace(hour=7, minute=0)
    if dt.hour >= 23:
        return dt.replace(hour=23, minute=0)
    return dt


def random_arrival(now, days):
    offset = random.randint(0, days * 24 * 60)
    dt = now + timedelta(minutes=offset)
    return clamp_to_garage_hours(snap_to_half_hour(dt))


def clustered_arrival(now, days):
    """
    Rush-hour clustered arrival.
    70% of arrivals fall in morning (8–9:30am) or evening (5–6:30pm) windows.
    30% are spread randomly through the day.
    """
    day_offset = random.randint(0, max(days - 1, 0))
    base = now + timedelta(days=day_offset)
    base = base.replace(second=0, microsecond=0)

    roll = random.random()

    if roll < 0.35:
        # Morning rush: 8:00–9:30am
        dt = base.replace(hour=8, minute=0) + timedelta(minutes=random.randint(0, 90))
    elif roll < 0.70:
        # Evening rush: 5:00–6:30pm
        dt = base.replace(hour=17, minute=0) + timedelta(minutes=random.randint(0, 90))
    else:
        # Random during garage hours
        dt = base.replace(hour=7, minute=0) + timedelta(minutes=random.randint(0, (23 - 7) * 60))

    return clamp_to_garage_hours(snap_to_half_hour(dt))


# ─────────────────────────────────────────────────────────────────────────────
#  Status distribution  —  80% confirmed, 10% expired, 10% cancelled
# ─────────────────────────────────────────────────────────────────────────────

STATUSES       = [ReservationStatusEnum.confirmed, ReservationStatusEnum.expired, ReservationStatusEnum.cancelled]
STATUS_WEIGHTS = [80, 10, 10]

DRIVER_CLASSES = ['standard', 'accessibility', 'employee', 'eco', None]
DRIVER_WEIGHTS = [60, 10, 15, 10, 5]


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    header("GARAGEFLOW RESERVATION SEEDER")
    print()

    # ── Step 1: deletion ──────────────────────────────────────────────────────
    print("  STEP 1 — Existing reservations")
    divider()

    with app.app_context():
        existing_count = Reservation.query.count()

    print(f"  There are currently {existing_count} reservation(s) in the database.")
    print()

    do_delete = False
    if existing_count > 0:
        do_delete = ask_yes_no("Delete all existing reservations before seeding?", default=False)
        if do_delete:
            print()
            print(f"  ⚠  This will permanently delete {existing_count} reservation(s).")
            confirm = input("  Type  CONFIRM DELETE  to proceed: ").strip()
            if confirm != "CONFIRM DELETE":
                print("\n  Deletion cancelled — existing reservations will be kept.")
                do_delete = False
            else:
                print("  ✔  Deletion confirmed.")

    # ── Step 2: phone mode ────────────────────────────────────────────────────
    print()
    print("  STEP 2 — Phone number format")
    divider()
    phone_mode = ask_choice(
        "How should phone numbers be generated?",
        [("random", "Random fake numbers  (e.g. 847-302-5591)"),
         ("debug",  "Debug sequential     (e.g. 0000000001, 0000000002 …)")]
    )

    # ── Step 3: count ─────────────────────────────────────────────────────────
    print()
    print("  STEP 3 — How many reservations?")
    divider()
    count = ask_int("Number to create (1–500)", min_val=1, max_val=500)

    # ── Step 4: time window ───────────────────────────────────────────────────
    print()
    print("  STEP 4 — Arrival time window")
    divider()
    window_days = ask_choice(
        "Spread arrivals across …",
        [("1",  "Next 24 hours"),
         ("7",  "Next 7 days"),
         ("30", "Next 30 days")]
    )
    window_days = int(window_days)

    # ── Step 5: distribution ─────────────────────────────────────────────────
    print()
    print("  STEP 5 — Arrival time distribution")
    divider()
    distribution = ask_choice(
        "How should arrival times be distributed?",
        [("random",    "Purely random across the window"),
         ("clustered", "Rush-hour clustered  (70% at 8–9:30am or 5–6:30pm)")]
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    header("SUMMARY")
    print(f"  Reservations to create : {count}")
    print(f"  Phone mode             : {phone_mode}")
    print(f"  Arrival window         : next {window_days} day(s)")
    print(f"  Distribution           : {distribution}")
    print(f"  Status mix             : 80% confirmed / 10% expired / 10% cancelled")
    if do_delete:
        print(f"  ⚠  Will delete         : {existing_count} existing reservation(s)")
    print()

    if not ask_yes_no("Commit to database?", default=True):
        print("\n  Cancelled. No changes made.\n")
        sys.exit(0)

    # ── Write ─────────────────────────────────────────────────────────────────
    print()
    print("  Writing to database …")

    now = datetime.utcnow()

    with app.app_context():
        if do_delete:
            Reservation.query.delete()
            db.session.commit()
            print(f"  🗑  Deleted {existing_count} existing reservation(s).")

        floors = Floor.query.order_by(Floor.floor_number).all()
        if not floors:
            print("\n  ⚠  No floors found. Run garage_builder.py first.\n")
            sys.exit(1)
        floor_numbers = [f.floor_number for f in floors]

        status_tally = {s: 0 for s in STATUSES}

        for i in range(1, count + 1):
            phone   = debug_phone(i) if phone_mode == "debug" else random_phone()
            arrival = clustered_arrival(now, window_days) if distribution == "clustered" else random_arrival(now, window_days)
            status  = random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
            status_tally[status] += 1

            db.session.add(Reservation(
                phone=phone,
                start_datetime=arrival,
                floor_number=random.choice(floor_numbers),
                status=status,
                customer_id=None,
                vehicle_id=None,
            ))

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"\n  ❌  Database error: {e}\n")
            sys.exit(1)

    # ── Done ──────────────────────────────────────────────────────────────────
    window_end = (now + timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M")

    print()
    divider("═")
    print(f"  ✅  Created {count} reservations")
    print(f"      Window   : {now.strftime('%Y-%m-%d %H:%M')}  →  {window_end}")
    print(f"      Floors   : {', '.join(str(f) for f in floor_numbers)}")
    print(f"      confirmed: {status_tally[ReservationStatusEnum.confirmed]}")
    print(f"      expired  : {status_tally[ReservationStatusEnum.expired]}")
    print(f"      cancelled: {status_tally[ReservationStatusEnum.cancelled]}")
    divider("═")
    print()


if __name__ == "__main__":
    main()
