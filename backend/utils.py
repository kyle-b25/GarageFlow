"""
utils.py — GarageFlow shared helpers

Consolidates duplicated logic from app.py, routes/_routes.py, and routes/payments.py.
"""

import math
import sys
from functools import wraps

from datetime import datetime

from flask import g, request, jsonify


def log_error(source, description):
    """
    Log an error to the SystemEvent table with stderr fallback.

    Always prints to stderr so audit trail survives even when the database
    is unreachable.  The caller is expected to have already rolled back any
    failed transaction before calling this function.
    """
    print(f"[ERROR] {source}: {description}", file=sys.stderr)
    try:
        from app import db
        from models import SystemEvent
        db.session.add(SystemEvent(source=source, description=description))
        db.session.commit()
    except Exception:
        db.session.rollback()


def calculate_duration(entry_ts, exit_ts):
    """Return parking duration in whole minutes (rounded up)."""
    return math.ceil((exit_ts - entry_ts).total_seconds() / 60)


def _parse_applicable_hours(applicable_hours):
    """Parse an applicable_hours string like '06:00-22:00' into (start_hour, end_hour).

    Returns (0, 24) if the string is unparseable or covers all hours (e.g. '24/7').
    Supports formats: 'HH:MM-HH:MM', '24/7', 'all'.
    """
    if not applicable_hours:
        return 0, 24
    cleaned = applicable_hours.strip().lower()
    if cleaned in ('24/7', 'all', ''):
        return 0, 24
    try:
        parts = cleaned.split('-')
        if len(parts) != 2:
            return 0, 24
        start_h = int(parts[0].split(':')[0])
        end_h = int(parts[1].split(':')[0])
        return start_h, end_h
    except (ValueError, IndexError):
        return 0, 24


def calculate_fee(entry_ts, exit_ts):
    """Calculate parking fee using tiered, time-window-based pricing (Option B: prorated).

    Algorithm — for every clock-hour segment of the session:
      1. Find the first PricingRule (ordered by rate_id, lowest = highest priority)
         whose applicable_hours window covers that clock hour.
      2. If no rule matches, use the garage's base_rate_per_hour.
      3. Multiply the winning rate by the exact fractional hours in that segment.
      4. Sum all segments and round up to the nearest cent.

    This means a vehicle parked 10:30–13:00 with rules:
      • base $2/hr, rule A 10:00-15:00 $9/hr (rate_id=1), rule B 11:00-13:00 $10/hr (rate_id=2)
    pays: 0.5 h × $9 (10:30-11:00) + 1 h × $9 (11:00-12:00) + 1 h × $9 (12:00-13:00) = $22.50
    Rule B never fires because rule A (lower rate_id) is checked first.

    Falls back to $2.00/hr if the DB is unreachable.
    """
    from datetime import timedelta
    from decimal import Decimal, ROUND_UP

    _FALLBACK_RATE = Decimal('2.00')

    try:
        from models import PricingRule, Garage
        garage = Garage.query.first()
        base_rate = (
            Decimal(str(garage.base_rate_per_hour))
            if garage and garage.base_rate_per_hour is not None
            else _FALLBACK_RATE
        )
        # Lower sort_order = higher priority (user-controlled via move up/down)
        rules = PricingRule.query.order_by(PricingRule.sort_order, PricingRule.rate_id).all()
    except Exception as exc:
        log_error('calculate_fee', f'DB error, using fallback rate: {exc}')
        seconds = (exit_ts - entry_ts).total_seconds()
        return (_FALLBACK_RATE * Decimal(str(seconds / 3600))).quantize(
            Decimal('0.01'), rounding=ROUND_UP
        )

    # Resolve Eastern timezone for rule-window matching.
    # entry_ts / exit_ts are naive UTC; convert each segment cursor to
    # America/New_York (handles EST/EDT automatically) before checking hours.
    _eastern = None
    try:
        from zoneinfo import ZoneInfo
        _eastern = ZoneInfo('America/New_York')
    except ImportError:
        try:
            import pytz
            _eastern = pytz.timezone('America/New_York')
        except ImportError:
            pass  # last resort: fall through and use UTC hour

    from datetime import timezone as _utc_tz

    total = Decimal('0.00')
    cursor = entry_ts

    while cursor < exit_ts:
        # Advance cursor to the top of the next clock hour
        next_boundary = (
            cursor.replace(minute=0, second=0, microsecond=0)
            + timedelta(hours=1)
        )
        segment_end = min(next_boundary, exit_ts)
        fraction = Decimal(str((segment_end - cursor).total_seconds())) / Decimal('3600')

        # Determine Eastern local hour for this segment
        if _eastern:
            h = cursor.replace(tzinfo=_utc_tz.utc).astimezone(_eastern).hour
        else:
            h = cursor.hour  # UTC fallback if no tz library is available

        # First rule whose window covers this local hour wins
        rate = base_rate
        for rule in rules:
            s, e = _parse_applicable_hours(rule.applicable_hours)
            # Normal window (e.g. 10-15): s <= h < e
            # Overnight window (e.g. 22-06): h >= s OR h < e
            in_window = (s <= h < e) if s <= e else (h >= s or h < e)
            if in_window:
                rate = Decimal(str(rule.rate_per_hour))
                break

        total += rate * fraction
        cursor = segment_end

    fee = total.quantize(Decimal('0.01'), rounding=ROUND_UP)
    # Enforce minimum charge — never less than one unit of the base rate
    return max(fee, base_rate.quantize(Decimal('0.01')))


def get_current_user():
    """
    Validate the Bearer token from the Authorization header.
    Returns the Staff object on success, or None on failure.
    Sets g.current_user and g.session_token as side effects.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None

    token_str = auth_header[7:]
    if not token_str:
        return None

    from sqlalchemy.orm import joinedload
    from models import SessionToken
    session_token = SessionToken.query.options(
        joinedload(SessionToken.staff)
    ).filter_by(
        token=token_str, is_active=True
    ).first()

    if not session_token or session_token.expires_at < datetime.utcnow():
        return None

    if not session_token.staff.is_active:
        return None

    g.current_user = session_token.staff
    g.session_token = session_token
    return session_token.staff


def login_required(f):
    """Decorator — rejects requests without a valid Bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated


def require_role(role_name):
    """Decorator factory — checks the authenticated user's role.

    Supported role_name values:
      "admin"       — requires StaffRoleEnum.admin
      "super_admin" — requires admin role AND is_super_admin flag set on Staff
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from models import StaffRoleEnum
            user = get_current_user()
            if not user:
                return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
            if role_name == 'super_admin':
                if user.role != StaffRoleEnum.admin or not user.is_super_admin:
                    return jsonify({'error': 'forbidden'}), 403
            elif role_name == 'admin':
                if user.role != StaffRoleEnum.admin:
                    return jsonify({'error': 'forbidden'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ------------------------------------------------------------------
#  Spot assignment constants & helpers
# ------------------------------------------------------------------

_DRIVER_CLASS_TO_SPOT_TYPE = {
    'standard':      'standard',
    'accessibility': 'accessibility',
    'employee':      'staff',
    'eco':           'staff',
}

VALID_DRIVER_CLASSES = set(_DRIVER_CLASS_TO_SPOT_TYPE.keys())

_SPOT_TYPE_TO_DRIVER_CLASSES = {}
for _dc, _st in _DRIVER_CLASS_TO_SPOT_TYPE.items():
    _SPOT_TYPE_TO_DRIVER_CLASSES.setdefault(_st, set()).add(_dc)

# Vehicle type → compatible spot types.  Motorcycles fit anywhere a car
# fits; trucks are restricted to standard-sized spots only (no compact
# accessibility or staff/eco spots which are typically smaller bays).
_VEHICLE_TYPE_COMPATIBLE_SPOTS = {
    'car':        {'standard', 'accessibility', 'staff', 'eco'},
    'motorcycle': {'standard', 'accessibility', 'staff', 'eco'},
    'truck':      {'standard'},
}


# ------------------------------------------------------------------
#  Spot assignment exceptions
# ------------------------------------------------------------------

class GarageFullError(Exception):
    """No available spots of any type remain in the garage."""
    http_status = 409
    error_key = "garage_full"


class NoSpotAvailableError(Exception):
    """No available spot of the requested type exists on any floor."""
    http_status = 409
    error_key = "no_spot_available"


class ReservationConflictError(Exception):
    """Every candidate spot conflicts with a confirmed reservation."""
    http_status = 409
    error_key = "reservation_conflict"


def assign_spot(driver_class, arrival_datetime=None, vehicle_type=None, garage_id=None):
    """
    Find the best available (spot, floor) pair for the given driver class.

    Floors are sorted by floor_number ascending (prefer lowest floor).
    When arrival_datetime is provided, candidate spots are checked against
    confirmed reservations within a ±30-minute window; floors where all
    spots of the requested type conflict are skipped.

    Args:
        driver_class:     One of VALID_DRIVER_CLASSES.
        arrival_datetime: Optional naive-UTC datetime for conflict checks.
        vehicle_type:     Optional VehicleTypeEnum value string ('car',
                          'motorcycle', 'truck').  When provided, only spots
                          whose type is compatible with the vehicle size are
                          considered.
        garage_id:        Optional int — restrict search to a single garage.

    Returns (spot, floor, reason) on success or failure.
    reason is None on success, or one of:
      'garage_full'        — no floors with available spots
      'no_spot_type'       — no spots of the requested type exist in the garage
      'spots_occupied'     — spots of the type exist but are all occupied
      'reservation_conflict' — all candidates conflict with reservations
      'vehicle_incompatible' — vehicle type incompatible with spot type
    """
    from models import (
        Floor, ParkingSpot, Reservation,
        SpotTypeEnum, SpotStatusEnum, ReservationStatusEnum,
    )

    spot_type_val = SpotTypeEnum(_DRIVER_CLASS_TO_SPOT_TYPE[driver_class])

    # If vehicle_type is specified, verify the requested spot type is
    # compatible with the vehicle.  If not, return early.
    if vehicle_type:
        compatible = _VEHICLE_TYPE_COMPATIBLE_SPOTS.get(vehicle_type)
        if compatible and spot_type_val.value not in compatible:
            return None, None, 'vehicle_incompatible'

    floor_q = Floor.query.filter(Floor.available_spots > 0)
    if garage_id is not None:
        floor_q = floor_q.filter(Floor.garage_id == garage_id)
    floors = [f for f in floor_q.all() if f.total_spots > 0]
    if not floors:
        return None, None, 'garage_full'

    floors.sort(key=lambda f: f.floor_number)

    matching_driver_classes = _SPOT_TYPE_TO_DRIVER_CLASSES.get(
        spot_type_val.value, set()
    )

    # Check if any spots of this type exist at all (regardless of status)
    type_exists_q = ParkingSpot.query.join(Floor).filter(
        ParkingSpot.spot_type == spot_type_val,
    )
    if garage_id is not None:
        type_exists_q = type_exists_q.filter(Floor.garage_id == garage_id)
    type_exists = type_exists_q.first() is not None

    if not type_exists:
        return None, None, 'no_spot_type'

    found_type_match = False

    for floor in floors:
        candidates = ParkingSpot.query.filter_by(
            floor_id=floor.floor_id,
            spot_type=spot_type_val,
            status=SpotStatusEnum.available,
        ).all()

        if not candidates:
            continue

        found_type_match = True

        # No conflict check needed — return first candidate
        if not arrival_datetime or not matching_driver_classes:
            return candidates[0], floor, None

        # Reservation conflict filtering (±30 min window)
        from datetime import timedelta
        window_start = arrival_datetime - timedelta(minutes=30)
        window_end = arrival_datetime + timedelta(minutes=30)

        from app import db
        conflicting_count = (
            db.session.query(db.func.count(Reservation.reservation_id))
            .filter(
                Reservation.status == ReservationStatusEnum.confirmed,
                Reservation.floor_number == floor.floor_number,
                Reservation.driver_class.in_(matching_driver_classes),
                Reservation.start_datetime >= window_start,
                Reservation.start_datetime <= window_end,
            )
            .scalar()
        )

        if len(candidates) - conflicting_count > 0:
            return candidates[0], floor, None

    if not found_type_match:
        return None, None, 'spots_occupied'
    return None, None, 'reservation_conflict'


def validate_and_assign_spot(garage_id, spot_type, arrival_datetime=None):
    """
    Select an available parking spot with conflict checking.

    This is a stricter version of assign_spot() that raises exceptions
    instead of returning (None, None). Used by tests and any code that
    needs to distinguish between garage-full, no-type-available, and
    reservation-conflict scenarios.

    Args:
        garage_id: Primary key of the target Garage.
        spot_type: A SpotTypeEnum member.
        arrival_datetime: Optional datetime for reservation conflict check.

    Returns:
        The chosen ParkingSpot ORM instance.

    Raises:
        GarageFullError, NoSpotAvailableError, ReservationConflictError
    """
    from app import db
    from models import (
        Floor, ParkingSpot, Reservation,
        SpotStatusEnum, ReservationStatusEnum,
    )

    # Step 1: total garage capacity
    available_count = (
        db.session.query(db.func.count(ParkingSpot.spot_id))
        .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
        .filter(
            Floor.garage_id == garage_id,
            ParkingSpot.status == SpotStatusEnum.available,
        )
        .scalar()
    )
    if available_count == 0:
        raise GarageFullError("Garage is full — no available spots")

    # Step 2: find any available spot of the requested type
    type_available = (
        db.session.query(db.func.count(ParkingSpot.spot_id))
        .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
        .filter(
            Floor.garage_id == garage_id,
            ParkingSpot.status == SpotStatusEnum.available,
            ParkingSpot.spot_type == spot_type,
        )
        .scalar()
    )
    if type_available == 0:
        raise NoSpotAvailableError(
            f"No available {spot_type.value} spot in garage {garage_id}"
        )

    # Step 3: reverse-map spot_type to driver_class for assign_spot
    matching_classes = _SPOT_TYPE_TO_DRIVER_CLASSES.get(spot_type.value, set())
    if not matching_classes:
        raise NoSpotAvailableError(
            f"No driver class maps to {spot_type.value}"
        )
    driver_class = next(iter(matching_classes))

    spot, floor, reason = assign_spot(driver_class, arrival_datetime=arrival_datetime)
    if spot:
        return spot

    if reason == 'no_spot_type':
        raise NoSpotAvailableError(
            f"No {spot_type.value} spots exist in garage {garage_id}"
        )
    if reason == 'reservation_conflict':
        raise ReservationConflictError(
            f"All {spot_type.value} spots conflict with confirmed reservations"
        )
    raise NoSpotAvailableError(
        f"No available {spot_type.value} spot in garage {garage_id}"
    )


def release_spot(spot_id, exit_ts):
    """
    Free a parking spot: set status to available, increment floor counter,
    append OccupancyLog. Does NOT commit — caller owns the transaction.

    Returns the ParkingSpot on success, or None if spot_id not found.
    """
    from app import db
    from models import ParkingSpot, Floor, OccupancyLog, SpotStatusEnum, OccupancyChangeEnum

    spot = ParkingSpot.query.get(spot_id)
    if not spot:
        return None

    if spot.status == SpotStatusEnum.available:
        return spot

    spot.status = SpotStatusEnum.available

    floor = Floor.query.get(spot.floor_id)
    if floor:
        floor.available_spots += 1

    db.session.add(OccupancyLog(
        spot_id=spot.spot_id,
        changed_at=exit_ts,
        change_type=OccupancyChangeEnum.freed,
    ))
    return spot


def safe_int(value, field_name="id"):
    """Parse value to int, returning None on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
