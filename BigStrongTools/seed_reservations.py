"""
seed_reservations.py — GarageFlow Reservation Seeder

Run this from your garageflow backend folder:
    python seed_reservations.py
"""

import sys
import os
import random
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, 'backend'))

try:
    from app import app, db
    from models import (
        Reservation, ReservationStatusEnum, Floor,
        Customer, Vehicle, VehicleTypeEnum, AccountStatusEnum,
    )
except ImportError as e:
    print(f"\n❌  Could not import GarageFlow modules: {e}")
    print("    Make sure you run this script from your garageflow backend folder.")
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
#  Phone / plate generators
# ─────────────────────────────────────────────────────────────────────────────

def random_phone():
    return f"{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"

def debug_phone(n):
    return str(n).zfill(10)

def random_plate():
    import string
    letters = ''.join(random.choices(string.ascii_uppercase, k=3))
    digits  = ''.join(random.choices(string.digits, k=4))
    return f"R{letters}-{digits}"   # R prefix avoids collisions with ticket seeder plates


# ─────────────────────────────────────────────────────────────────────────────
#  Arrival time generators
# ─────────────────────────────────────────────────────────────────────────────

def snap_to_half_hour(dt):
    return dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)

def clamp_to_garage_hours(dt):
    if dt.hour < 7:
        return dt.replace(hour=7, minute=0)
    if dt.hour >= 23:
        return dt.replace(hour=23, minute=0)
    return dt

def random_arrival(now, days):
    dt = now + timedelta(minutes=random.randint(0, days * 24 * 60))
    return clamp_to_garage_hours(snap_to_half_hour(dt))

def clustered_arrival(now, days):
    base = (now + timedelta(days=random.randint(0, max(days - 1, 0)))).replace(second=0, microsecond=0)
    roll = random.random()
    if roll < 0.35:
        dt = base.replace(hour=8,  minute=0) + timedelta(minutes=random.randint(0, 90))
    elif roll < 0.70:
        dt = base.replace(hour=17, minute=0) + timedelta(minutes=random.randint(0, 90))
    else:
        dt = base.replace(hour=7,  minute=0) + timedelta(minutes=random.randint(0, 16 * 60))
    return clamp_to_garage_hours(snap_to_half_hour(dt))


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

STATUSES       = [ReservationStatusEnum.confirmed, ReservationStatusEnum.expired, ReservationStatusEnum.cancelled]
STATUS_WEIGHTS = [80, 10, 10]

DRIVER_CLASSES = ['standard', 'accessibility', 'employee', 'eco', None]
DRIVER_WEIGHTS = [60, 10, 15, 10, 5]

PLATE_STATES   = ['NY', 'NJ', 'CT', 'PA', 'MA', 'FL', 'CA', 'TX']
VEHICLE_TYPES  = [VehicleTypeEnum.car, VehicleTypeEnum.car, VehicleTypeEnum.car,
                  VehicleTypeEnum.truck, VehicleTypeEnum.motorcycle]

STAY_HOURS = 2   # default reservation window


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
            confirm = input("  Type  CONFIRM DELETE  to proceed: ").strip()
            if confirm != "CONFIRM DELETE":
                print("\n  Deletion cancelled.")
                do_delete = False

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
    window_days = int(ask_choice(
        "Spread arrivals across …",
        [("1", "Next 24 hours"), ("7", "Next 7 days"), ("30", "Next 30 days")]
    ))

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
        floors = Floor.query.order_by(Floor.floor_number).all()
        if not floors:
            print("\n  ⚠  No floors found. Run seed.py or garage_builder.py first.\n")
            sys.exit(1)
        floor_numbers = [f.floor_number for f in floors]

        if do_delete:
            Reservation.query.delete()
            db.session.commit()
            print(f"  🗑  Deleted {existing_count} existing reservation(s).")

        # Pre-collect existing plates to avoid unique constraint collisions
        existing_plates = {
            row[0] for row in db.session.execute(db.text('SELECT license_plate FROM vehicle')).fetchall()
        }

        status_tally = {s: 0 for s in STATUSES}

        for i in range(1, count + 1):
            phone        = debug_phone(i) if phone_mode == "debug" else random_phone()
            arrival      = clustered_arrival(now, window_days) if distribution == "clustered" else random_arrival(now, window_days)
            end_dt       = arrival + timedelta(hours=STAY_HOURS)
            status       = random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
            driver_class = random.choices(DRIVER_CLASSES, weights=DRIVER_WEIGHTS, k=1)[0]
            status_tally[status] += 1

            # ── Customer (get-or-create by phone) ─────────────────────────────
            customer = Customer.query.filter_by(phone_number=phone).first()
            if not customer:
                customer = Customer(
                    name=f"Seed Customer {i}",
                    email=f"seed_{i}_{random.randint(1000,9999)}@placeholder.local",
                    phone_number=phone,
                    account_status=AccountStatusEnum.active,
                )
                db.session.add(customer)
                db.session.flush()

            # ── Vehicle (unique plate per reservation) ────────────────────────
            plate = random_plate()
            while plate in existing_plates:
                plate = random_plate()
            existing_plates.add(plate)

            vehicle = Vehicle(
                license_plate=plate,
                plate_state=random.choice(PLATE_STATES),
                vehicle_type=random.choice(VEHICLE_TYPES),
                customer_id=customer.customer_id,
            )
            db.session.add(vehicle)
            db.session.flush()

            # ── Reservation ───────────────────────────────────────────────────
            db.session.add(Reservation(
                phone=phone,
                driver_class=driver_class,
                start_datetime=arrival,
                end_datetime=end_dt,        # NOT NULL — 2h window
                quoted_fee=0,               # NOT NULL — no pricing engine at seed time
                customer_id=customer.customer_id,
                vehicle_id=vehicle.vehicle_id,
                floor_number=random.choice(floor_numbers),
                status=status,
            ))

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"\n  ❌  Database error: {e}\n")
            sys.exit(1)

    window_end = (now + timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M")
    print()
    divider("═")
    print(f"  ✅  Created {count} reservations")
    print(f"      Window    : {now.strftime('%Y-%m-%d %H:%M')}  →  {window_end}")
    print(f"      confirmed : {status_tally[ReservationStatusEnum.confirmed]}")
    print(f"      expired   : {status_tally[ReservationStatusEnum.expired]}")
    print(f"      cancelled : {status_tally[ReservationStatusEnum.cancelled]}")
    divider("═")
    print()


if __name__ == "__main__":
    main()
