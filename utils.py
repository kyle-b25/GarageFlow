"""
utils.py — GarageFlow shared helpers

Consolidates duplicated logic from app.py, routes/_routes.py, and routes/payments.py.
"""

import math
import sys
from decimal import Decimal
from functools import wraps

from flask import session, jsonify


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


def login_required(f):
    """Decorator — rejects requests without an active staff session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'operator_id' not in session:
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator — rejects requests without an active admin session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'operator_id' not in session:
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': 'forbidden', 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated
