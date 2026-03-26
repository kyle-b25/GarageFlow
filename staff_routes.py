"""
staff_routes.py — GarageFlow Staff Blueprint

All routes registered on this blueprint require an authenticated staff session.
Admin-only routes additionally use the @require_admin decorator.

Exports:
  staff_bp      — Flask Blueprint; register with app.register_blueprint()
  require_admin — decorator for admin-only route functions
"""

from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
import bcrypt
from flask import Blueprint, request, jsonify, g

from utils import _get_current_user

staff_bp    = Blueprint('staff', __name__)
_VALID_ROLES = {'admin', 'attendant'}


# ------------------------------------------------------------------
#  Authentication middleware
# ------------------------------------------------------------------

@staff_bp.before_request
def require_auth():
    """Reject any request that arrives without a valid Bearer token."""
    if not _get_current_user():
        return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401


# ------------------------------------------------------------------
#  Decorators
# ------------------------------------------------------------------

def require_admin(f):
    """
    Route decorator that restricts access to admin-role tokens.
    Apply after @staff_bp.route (i.e., closer to the function definition).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.current_user.role.value != 'admin':
            return jsonify({'error': 'forbidden', 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# ------------------------------------------------------------------
#  POST /v1/staff  (admin only)
# ------------------------------------------------------------------

@staff_bp.route('/v1/staff', methods=['POST'])
@require_admin
def create_staff():
    """
    POST /v1/staff  (admin only)

    Create a new staff account. The password is bcrypt-hashed before storage.
    Role defaults to 'attendant' if omitted.
    """
    from app import db
    from models import Staff, StaffRoleEnum

    data     = request.get_json(silent=True) or {}
    name     = data.get('name')
    username = data.get('username')
    password = data.get('password')
    role_str = data.get('role', 'attendant')

    if not name:
        return jsonify({'error': 'missing_required_field', 'message': 'name is required'}), 400
    if not username:
        return jsonify({'error': 'missing_required_field', 'message': 'username is required'}), 400
    if not password:
        return jsonify({'error': 'missing_required_field', 'message': 'password is required'}), 400
    if role_str not in _VALID_ROLES:
        return jsonify({
            'error':   'invalid_role',
            'message': f'role must be one of: {", ".join(sorted(_VALID_ROLES))}',
        }), 400

    if Staff.query.filter_by(username=username).first():
        return jsonify({
            'error':   'duplicate_username',
            'message': 'A staff member with that username already exists',
        }), 409

    password_hash = bcrypt.hashpw(
        password.encode('utf-8'), bcrypt.gensalt()
    ).decode('utf-8')

    staff = Staff(
        name=name,
        username=username,
        password_hash=password_hash,
        role=StaffRoleEnum[role_str],
    )
    try:
        db.session.add(staff)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error', 'message': 'Failed to create staff member'}), 500

    return jsonify({
        'operator_id': staff.operator_id,
        'name':        staff.name,
        'username':    staff.username,
        'role':        staff.role.value,
    }), 201


# ------------------------------------------------------------------
#  Analytics helpers
# ------------------------------------------------------------------

def _parse_date_range():
    """
    Parse ?from and ?to query params as UTC datetimes.
    Defaults to the last 7 days if omitted.
    Returns (from_dt, to_dt) or raises ValueError with a message.
    """
    now = datetime.utcnow()
    raw_from = request.args.get('from')
    raw_to   = request.args.get('to')

    try:
        from_dt = datetime.fromisoformat(raw_from.replace('Z', '+00:00')).replace(tzinfo=None) if raw_from else now - timedelta(days=7)
    except (ValueError, AttributeError):
        raise ValueError('from is not a valid ISO 8601 datetime')

    try:
        to_dt = datetime.fromisoformat(raw_to.replace('Z', '+00:00')).replace(tzinfo=None) if raw_to else now
    except (ValueError, AttributeError):
        raise ValueError('to is not a valid ISO 8601 datetime')

    if from_dt >= to_dt:
        raise ValueError('from must be before to')

    return from_dt, to_dt


# ------------------------------------------------------------------
#  GET /v1/analytics/utilization  (staff only)
# ------------------------------------------------------------------

@staff_bp.route('/v1/analytics/utilization', methods=['GET'])
def get_utilization():
    """
    GET /v1/analytics/utilization

    Returns daily entry and exit counts for the requested date range.
    Computed from the append-only OccupancyLog table.

    Query params:
      from  (optional) ISO 8601 datetime — defaults to 7 days ago
      to    (optional) ISO 8601 datetime — defaults to now

    Response:
      {
        "from": "...", "to": "...",
        "days": [
          {"date": "YYYY-MM-DD", "entries": N, "exits": N},
          ...
        ]
      }
    """
    from app import db
    from models import OccupancyLog, OccupancyChangeEnum

    try:
        from_dt, to_dt = _parse_date_range()
    except ValueError as exc:
        return jsonify({'error': 'invalid_date_range', 'message': str(exc)}), 400

    try:
        logs = OccupancyLog.query.filter(
            OccupancyLog.changed_at >= from_dt,
            OccupancyLog.changed_at <= to_dt,
        ).all()

        entries_by_day = defaultdict(int)
        exits_by_day   = defaultdict(int)

        for log in logs:
            day = log.changed_at.date().isoformat()
            if log.change_type == OccupancyChangeEnum.occupied:
                entries_by_day[day] += 1
            else:
                exits_by_day[day] += 1

        # Build a complete day-by-day series between from_dt and to_dt
        days = []
        cursor = from_dt.date()
        end    = to_dt.date()
        while cursor <= end:
            iso = cursor.isoformat()
            days.append({
                'date':    iso,
                'entries': entries_by_day.get(iso, 0),
                'exits':   exits_by_day.get(iso, 0),
            })
            cursor += timedelta(days=1)

        return jsonify({
            'from': from_dt.isoformat() + 'Z',
            'to':   to_dt.isoformat() + 'Z',
            'days': days,
        }), 200

    except Exception as exc:
        db.session.rollback()
        from models import SystemEvent
        db.session.add(SystemEvent(source='analytics.utilization', description=str(exc)))
        db.session.commit()
        return jsonify({'error': 'server_error', 'message': 'Failed to generate utilization report'}), 500


# ------------------------------------------------------------------
#  GET /v1/analytics/revenue  (staff only)
# ------------------------------------------------------------------

@staff_bp.route('/v1/analytics/revenue', methods=['GET'])
def get_revenue():
    """
    GET /v1/analytics/revenue

    Returns revenue totals and session statistics for closed tickets
    in the requested date range.

    Query params:
      from  (optional) ISO 8601 datetime — defaults to 7 days ago
      to    (optional) ISO 8601 datetime — defaults to now

    Response:
      {
        "from": "...", "to": "...",
        "totalRevenue": 0.00,
        "transactionCount": N,
        "averageFee": 0.00,
        "averageDurationMinutes": N
      }
    """
    from app import db
    from models import Ticket, TicketStatusEnum

    try:
        from_dt, to_dt = _parse_date_range()
    except ValueError as exc:
        return jsonify({'error': 'invalid_date_range', 'message': str(exc)}), 400

    try:
        tickets = Ticket.query.filter(
            Ticket.status == TicketStatusEnum.closed,
            Ticket.exit_timestamp >= from_dt,
            Ticket.exit_timestamp <= to_dt,
            Ticket.total_fee.isnot(None),
        ).all()

        count         = len(tickets)
        total_revenue = sum(float(t.total_fee) for t in tickets)
        total_minutes = sum(t.duration for t in tickets if t.duration is not None)

        return jsonify({
            'from':                    from_dt.isoformat() + 'Z',
            'to':                      to_dt.isoformat() + 'Z',
            'totalRevenue':            round(total_revenue, 2),
            'transactionCount':        count,
            'averageFee':              round(total_revenue / count, 2) if count else 0.00,
            'averageDurationMinutes':  round(total_minutes / count) if count else 0,
        }), 200

    except Exception as exc:
        db.session.rollback()
        from models import SystemEvent
        db.session.add(SystemEvent(source='analytics.revenue', description=str(exc)))
        db.session.commit()
        return jsonify({'error': 'server_error', 'message': 'Failed to generate revenue report'}), 500


# ------------------------------------------------------------------
#  GET /v1/analytics/peak-hours  (staff only)
# ------------------------------------------------------------------

@staff_bp.route('/v1/analytics/peak-hours', methods=['GET'])
def get_peak_hours():
    """
    GET /v1/analytics/peak-hours

    Returns the number of vehicle entries for each hour of the day (0–23),
    aggregated across all tickets in the requested date range.

    Query params:
      from  (optional) ISO 8601 datetime — defaults to 7 days ago
      to    (optional) ISO 8601 datetime — defaults to now

    Response:
      {
        "from": "...", "to": "...",
        "hours": [
          {"hour": 0, "entries": N},
          ...,
          {"hour": 23, "entries": N}
        ]
      }
    """
    from app import db
    from models import Ticket

    try:
        from_dt, to_dt = _parse_date_range()
    except ValueError as exc:
        return jsonify({'error': 'invalid_date_range', 'message': str(exc)}), 400

    try:
        tickets = Ticket.query.filter(
            Ticket.entry_timestamp >= from_dt,
            Ticket.entry_timestamp <= to_dt,
        ).all()

        counts = defaultdict(int)
        for ticket in tickets:
            counts[ticket.entry_timestamp.hour] += 1

        hours = [{'hour': h, 'entries': counts.get(h, 0)} for h in range(24)]

        return jsonify({
            'from':  from_dt.isoformat() + 'Z',
            'to':    to_dt.isoformat() + 'Z',
            'hours': hours,
        }), 200

    except Exception as exc:
        db.session.rollback()
        from models import SystemEvent
        db.session.add(SystemEvent(source='analytics.peak_hours', description=str(exc)))
        db.session.commit()
        return jsonify({'error': 'server_error', 'message': 'Failed to generate peak-hours report'}), 500
