"""
seed_tickets_48h.py — GarageFlow Historical Ticket Seeder (fixed)
==================================================================
Generates closed tickets spaced 10 minutes apart across the last 48 hours,
each with a 5-hour stay.  Every ticket gets a matching vehicle, payment,
and occupancy_log pair (occupied + freed).

Usage (it works when I run it from inside BSTools):
    python seed_past_tickets.py
"""

import argparse
import random
import sqlite3
import string
import sys
import os
from datetime import datetime, timedelta


PROJECT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
os.chdir(PROJECT_DIR)
#I'm the goat
#      -Sam Gibney

# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
INTERVAL_MINUTES = 30
STAY_HOURS       = 5
BASE_FEE         = 5.00
HOURLY_RATE      = 2.00
DURATION_MINUTES = STAY_HOURS * 60          # 300
FEE              = BASE_FEE + HOURLY_RATE * STAY_HOURS  # $15.00

# Must match VehicleTypeEnum exactly: car | motorcycle | truck
VEHICLE_TYPES   = ['car', 'car', 'car', 'car', 'truck', 'motorcycle']
PAYMENT_METHODS = ['card', 'card', 'cash', 'mobile']
PLATE_STATES    = ['NY', 'NJ', 'CT', 'PA', 'MA', 'FL', 'CA', 'TX']


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def random_plate() -> str:
    letters = ''.join(random.choices(string.ascii_uppercase, k=3))
    digits  = ''.join(random.choices(string.digits, k=4))
    return f'{letters}-{digits}'


def resolve_db_path(db_path: str) -> str:
    """If db_path is a directory, find the first .db file inside it."""
    import os
    if os.path.isdir(db_path):
        db_files = [f for f in os.listdir(db_path) if f.endswith('.db')]
        if not db_files:
            sys.exit(f'ERROR: No .db file found in directory: {db_path}')
        if len(db_files) > 1:
            print(f'WARNING: Multiple .db files found, using: {db_files[0]}')
        resolved = os.path.join(db_path, db_files[0])
        print(f'Using database: {resolved}')
        return resolved
    if not os.path.exists(db_path):
        sys.exit(f'ERROR: Database file not found: {db_path}\n'
                 f'Hint: pass the full path, e.g. --db instance/garageflow.db')
    return db_path


def connect(db_path: str) -> sqlite3.Connection:
    db_path = resolve_db_path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def fetch_one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def fetch_all(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
#  Core logic
# ---------------------------------------------------------------------------

def run(db_path: str, dry_run: bool):
    conn = connect(db_path)

    now   = datetime.now(tz=None).replace(second=0, microsecond=0)
    start = now - timedelta(hours=192)

    # ── Pre-flight checks ──────────────────────────────────────────────────

    # Verify schema exists before querying application tables
    tables = {
        row[0] for row in fetch_all(
            conn, "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required = {'garage', 'floor', 'parking_spot', 'gate_event',
                'vehicle', 'ticket', 'payment', 'occupancy_log'}
    missing = required - tables
    if missing:
        print('\nERROR: These tables are missing from the database:')
        for t in sorted(missing):
            print(f'  - {t}')
        print('\nThe schema has not been initialised yet.')
        print('Run from your backend folder:')
        print('  python -c "from app import app, db; app.app_context().push(); db.create_all()"')
        print('Then re-run this script.')
        conn.close()
        sys.exit(1)

    garage = fetch_one(conn, 'SELECT garage_id FROM garage LIMIT 1')
    if not garage:
        sys.exit('ERROR: No garage found. Seed the garage first.')
    garage_id = garage['garage_id']

    entry_gate = fetch_one(
        conn,
        "SELECT gate_id FROM gate_event WHERE garage_id=? AND gate_type='entry' LIMIT 1",
        (garage_id,),
    )
    exit_gate = fetch_one(
        conn,
        "SELECT gate_id FROM gate_event WHERE garage_id=? AND gate_type='exit' LIMIT 1",
        (garage_id,),
    )
    if not entry_gate or not exit_gate:
        sys.exit(
            'ERROR: Entry or exit gate missing from gate_event table.\n'
            'Your seed.py does not create gates. Run this SQL against your DB to add them:\n'
            '  INSERT INTO gate_event (garage_id, gate_type, status) VALUES (1, \'entry\', \'open\');\n'
            '  INSERT INTO gate_event (garage_id, gate_type, status) VALUES (1, \'exit\',  \'open\');'
        )

    entry_gate_id = entry_gate['gate_id']
    exit_gate_id  = exit_gate['gate_id']

    # Collect available spots (all floors, ordered naturally)
    spots = fetch_all(
        conn,
        """
        SELECT ps.spot_id, ps.floor_id, f.floor_number
        FROM   parking_spot ps
        JOIN   floor f ON f.floor_id = ps.floor_id
        WHERE  ps.status = 'available'
        ORDER  BY f.floor_number, ps.spot_id
        """,
    )
    if not spots:
        sys.exit('ERROR: No available spots found.')

    available_spots = [row['spot_id'] for row in spots]
    print(f'Found {len(available_spots)} available spots across all floors.')

    # ── Build entry time list ──────────────────────────────────────────────

    entry_times = []
    t = start
    while t <= now:
        entry_times.append(t)
        t += timedelta(minutes=INTERVAL_MINUTES)

    # Cap at available spots
    count = min(len(entry_times), len(available_spots))
    if len(entry_times) > len(available_spots):
        print(f'WARNING: Capping at {count} tickets (not enough spots for all intervals).')

    random.shuffle(available_spots)
    spot_pool   = available_spots[:count]
    entry_times = entry_times[:count]

    # ── Collect plates already in the DB so we don't duplicate ────────────

    existing_plates = {
        row[0] for row in conn.execute('SELECT license_plate FROM vehicle').fetchall()
    }

    # ── Dry-run short circuit ──────────────────────────────────────────────

    if dry_run:
        active_count  = sum(1 for et in entry_times if et + timedelta(hours=STAY_HOURS) > now)
        closed_count  = count - active_count
        print(f'\n── DRY RUN ──')
        print(f'  Would insert {count} tickets ({closed_count} closed, {active_count} active)')
        print(f'  {closed_count} payments, {count + closed_count} occupancy log rows')
        print(f'  Entry window: {entry_times[0]} → {entry_times[-1]} UTC')
        conn.close()
        return

    # ── Execute inside one transaction ────────────────────────────────────

    inserted_tickets  = 0
    inserted_payments = 0
    inserted_logs     = 0

    try:
        conn.execute('BEGIN')

        for entry_ts, spot_id in zip(entry_times, spot_pool):
            exit_ts = entry_ts + timedelta(hours=STAY_HOURS)
            is_closed = exit_ts <= now

            entry_str = entry_ts.strftime('%Y-%m-%d %H:%M:%S')
            exit_str  = exit_ts.strftime('%Y-%m-%d %H:%M:%S')

            # ── Vehicle ───────────────────────────────────────────────────
            plate = random_plate()
            while plate in existing_plates:
                plate = random_plate()
            existing_plates.add(plate)

            plate_state  = random.choice(PLATE_STATES)
            vehicle_type = random.choice(VEHICLE_TYPES)   # car|truck|motorcycle only

            conn.execute(
                'INSERT INTO vehicle (license_plate, plate_state, vehicle_type, customer_id) '
                'VALUES (?, ?, ?, NULL)',
                (plate, plate_state, vehicle_type),
            )
            vehicle_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

            # ── Ticket ────────────────────────────────────────────────────
            if is_closed:
                conn.execute(
                    'INSERT INTO ticket '
                    '(entry_timestamp, exit_timestamp, entry_gate_id, exit_gate_id, '
                    ' spot_id, vehicle_id, status, duration, total_fee, phone) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)',
                    (entry_str, exit_str,
                     entry_gate_id, exit_gate_id,
                     spot_id, vehicle_id,
                     'closed', DURATION_MINUTES, FEE),
                )
            else:
                conn.execute(
                    'INSERT INTO ticket '
                    '(entry_timestamp, exit_timestamp, entry_gate_id, exit_gate_id, '
                    ' spot_id, vehicle_id, status, duration, total_fee, phone) '
                    'VALUES (?, NULL, ?, NULL, ?, ?, ?, NULL, NULL, NULL)',
                    (entry_str,
                     entry_gate_id,
                     spot_id, vehicle_id,
                     'active'),
                )
            ticket_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            inserted_tickets += 1

            # ── Payment (closed tickets only) ─────────────────────────────
            if is_closed:
                pay_method = random.choice(PAYMENT_METHODS)
                conn.execute(
                    'INSERT INTO payment '
                    '(ticket_id, amount_charged, payment_method, '
                    ' payment_timestamp, payment_status, stripe_payment_intent_id) '
                    'VALUES (?, ?, ?, ?, ?, NULL)',
                    (ticket_id, FEE, pay_method, exit_str, 'paid'),
                )
                inserted_payments += 1

            # ── Occupancy log — entry event ───────────────────────────────
            conn.execute(
                'INSERT INTO occupancy_log (spot_id, changed_at, change_type) '
                'VALUES (?, ?, ?)',
                (spot_id, entry_str, 'occupied'),
            )
            inserted_logs += 1

            # ── Occupancy log — exit event (closed only) ──────────────────
            if is_closed:
                conn.execute(
                    'INSERT INTO occupancy_log (spot_id, changed_at, change_type) '
                    'VALUES (?, ?, ?)',
                    (spot_id, exit_str, 'freed'),
                )
                inserted_logs += 1

            # ── Spot status: active tickets occupy the spot ───────────────
            #    Closed tickets leave the spot available (history only)
            if not is_closed:
                conn.execute(
                    "UPDATE parking_spot SET status='occupied' WHERE spot_id=?",
                    (spot_id,),
                )
                # Decrement floor counter for active occupancy
                conn.execute(
                    """
                    UPDATE floor
                    SET    available_spots = available_spots - 1
                    WHERE  floor_id = (SELECT floor_id FROM parking_spot WHERE spot_id=?)
                      AND  available_spots > 0
                    """,
                    (spot_id,),
                )

        conn.execute('COMMIT')
        print(f'\n✓ Done.')
        print(f'  Tickets:       {inserted_tickets}')
        print(f'  Payments:      {inserted_payments}')
        print(f'  Occupancy log: {inserted_logs} rows')
        print(f'  Entry window:  {entry_times[0]} → {entry_times[-1]} UTC')

    except Exception as exc:
        conn.execute('ROLLBACK')
        print(f'\nERROR: Transaction rolled back.\n{exc}')
        sys.exit(1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Seed GarageFlow DB with 48h of historical ticket data.'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print counts without executing.',
    )
    parser.add_argument(
        '--db', default='instance/database.db',
        help='Path to the SQLite database file (default: instance/database.db).',
    )
    args = parser.parse_args()
    run(args.db, args.dry_run)
