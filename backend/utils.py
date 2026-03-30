"""
utils.py — GarageFlow shared helpers

Consolidates duplicated logic from app.py, routes/_routes.py, and routes/payments.py.
"""

import math
import sys
from decimal import Decimal
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


def calculate_fee(duration_minutes):
    """$5.00 base + $2.00 per hour (ceiling)."""
    return Decimal('5.00') + Decimal('2.00') * math.ceil(duration_minutes / 60)


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
      "super_admin" — requires admin role AND username == 'admin' (placeholder
                      until a real super-admin flag is added to the Staff model)
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from models import StaffRoleEnum
            user = get_current_user()
            if not user:
                return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
            if role_name == 'super_admin':
                if user.role != StaffRoleEnum.admin or user.username != 'admin':
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


def assign_spot(driver_class, arrival_datetime=None):
    """
    Find the best available (spot, floor) pair for the given driver class.

    Floors are sorted by floor_number ascending (prefer lowest floor).
    When arrival_datetime is provided, candidate spots are checked against
    confirmed reservations within a ±30-minute window; floors where all
    spots of the requested type conflict are skipped.

    Returns (spot, floor) on success, or (None, None) if garage is full.
    """
    from models import (
        Floor, ParkingSpot, Reservation,
        SpotTypeEnum, SpotStatusEnum, ReservationStatusEnum,
    )

    spot_type_val = SpotTypeEnum(_DRIVER_CLASS_TO_SPOT_TYPE[driver_class])

    floors = [f for f in Floor.query.filter(Floor.available_spots > 0).all()
              if f.total_spots > 0]
    if not floors:
        return None, None

    floors.sort(key=lambda f: f.floor_number)

    matching_driver_classes = _SPOT_TYPE_TO_DRIVER_CLASSES.get(
        spot_type_val.value, set()
    )

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
            return candidates[0], floor

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
            return candidates[0], floor

    return None, None


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

    spot, floor = assign_spot(driver_class, arrival_datetime=arrival_datetime)
    if spot:
        return spot

    # assign_spot returned None — must be a conflict
    if arrival_datetime:
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
