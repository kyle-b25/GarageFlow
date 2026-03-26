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


def _get_current_user():
    """
    Validate the Bearer token from the Authorization header.
    Returns the Staff object on success, or None on failure.
    Sets g.current_user as a side effect.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None

    token = auth_header[7:]
    if not token:
        return None

    from models import SessionToken
    session_token = SessionToken.query.filter_by(
        token=token, is_active=True
    ).first()

    if not session_token or session_token.expires_at < datetime.utcnow():
        return None

    g.current_user = session_token.staff
    return session_token.staff


def login_required(f):
    """Decorator — rejects requests without a valid Bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _get_current_user():
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator — rejects requests without a valid admin Bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if not user:
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        if user.role.value != 'admin':
            return jsonify({'error': 'forbidden', 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated
