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


def admin_required(f):
    """Decorator — rejects requests without a valid admin Bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        from models import StaffRoleEnum
        user = get_current_user()
        if not user:
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        if user.role != StaffRoleEnum.admin:
            return jsonify({'error': 'forbidden', 'message': 'Admin access required'}), 403
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


def assign_spot(driver_class):
    """
    Find the best available (spot, floor) pair for the given driver class.

    Floors are sorted by floor_number ascending (prefer lowest floor).

    Returns (spot, floor) on success, or (None, None) if garage is full.
    """
    from models import Floor, ParkingSpot, SpotTypeEnum, SpotStatusEnum

    spot_type_val = SpotTypeEnum(_DRIVER_CLASS_TO_SPOT_TYPE[driver_class])

    floors = [f for f in Floor.query.filter(Floor.available_spots > 0).all()
              if f.total_spots > 0]
    if not floors:
        return None, None

    floors.sort(key=lambda f: f.floor_number)

    for floor in floors:
        spot = ParkingSpot.query.filter_by(
            floor_id=floor.floor_id,
            spot_type=spot_type_val,
            status=SpotStatusEnum.available,
        ).first()
        if spot:
            return spot, floor

    return None, None


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
