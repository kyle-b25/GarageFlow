"""
admin_bp.py — GarageFlow Admin Management API Blueprint

Staff account CRUD, audit history, and report export endpoints.

Design decision — audit log:
  SystemEvent (with the new nullable staff_id FK) is used as the audit log.
  No dedicated model is needed: source + description + staff_id provide
  sufficient filtering (by user, action type, and date range).
"""
import csv
import io
from datetime import datetime
from decimal import Decimal

import bcrypt
from flask import Blueprint, jsonify, request, g, Response

from app import db
from models import Staff, StaffRoleEnum, SystemEvent, SessionToken
from utils import require_role, safe_int, log_error

admin_bp = Blueprint('admin', __name__, url_prefix='/v1')


# ── Serializers ───────────────────────────────────────────────────

def _user_json(staff):
    return {
        'operatorId': staff.operator_id,
        'name':     staff.name,
        'username': staff.username,
        'role':     staff.role.value,
        'isActive': staff.is_active,
    }


def _event_json(evt):
    return {
        'eventId':     evt.event_id,
        'staffId':     evt.staff_id,
        'source':      evt.source,
        'description': evt.description,
        'createdAt':   evt.created_at.isoformat() + 'Z' if evt.created_at else None,
    }


def _audit(source, description, staff_id=None):
    """Write a SystemEvent audit entry."""
    db.session.add(SystemEvent(
        source=source,
        description=description,
        staff_id=staff_id,
    ))


# ── GET /v1/users ─────────────────────────────────────────────────

@admin_bp.route('/users', methods=['GET'])
@require_role('admin')
def list_users():
    """List all staff accounts."""
    try:
        users = Staff.query.all()
        return jsonify([_user_json(u) for u in users]), 200
    except Exception as exc:
        log_error('admin.list_users', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to list users'}), 500


# ── GET /v1/users/<id> ────────────────────────────────────────────

@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@require_role('admin')
def get_user(user_id):
    """Return details of a single staff account."""
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found', 'message': 'No user found with that ID'}), 404
        return jsonify(_user_json(user)), 200
    except Exception as exc:
        log_error('admin.get_user', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch user'}), 500


# ── DELETE /v1/users/<id> ─────────────────────────────────────────

@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_role('super_admin')
def delete_user(user_id):
    """Delete a staff account if it has no audit history."""
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found', 'message': 'No user found with that ID'}), 404

        has_audit = SystemEvent.query.filter(
            db.or_(
                SystemEvent.staff_id == user_id,
                SystemEvent.description.contains(f'operator_id={user_id}'),
            )
        ).first()
        if has_audit:
            return jsonify({'error': 'delete_blocked_audit_history', 'message': 'Cannot delete user with audit history'}), 409

        operator_id = user.operator_id
        username = user.username
        db.session.delete(user)
        _audit('admin_bp.delete',
               f'Deleted staff account operator_id={operator_id} username={username}')
        db.session.commit()
        return jsonify({'message': 'user deleted'}), 200
    except Exception as exc:
        db.session.rollback()
        log_error('admin.delete_user', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to delete user'}), 500


# ── PUT /v1/users/<id>/role ───────────────────────────────────────

@admin_bp.route('/users/<int:user_id>/role', methods=['PUT'])
@require_role('admin')
def change_role(user_id):
    """Change a staff member's role."""
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found', 'message': 'No user found with that ID'}), 404

        data = request.get_json(silent=True) or {}
        role_val = data.get('role')
        if not role_val:
            return jsonify({'error': 'missing_required_field', 'message': 'role is required'}), 400

        try:
            new_role = StaffRoleEnum(role_val)
        except ValueError:
            return jsonify({'error': 'invalid_role', 'message': 'Unrecognized role value'}), 400

        old_role = user.role.value
        user.role = new_role
        _audit('admin_bp.role_change',
               f'Changed role for operator_id={user_id} from {old_role} to {new_role.value}',
               staff_id=user_id)
        db.session.commit()
        return jsonify(_user_json(user)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('admin.change_role', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to change role'}), 500


# ── PUT /v1/users/<id>/password ───────────────────────────────────

@admin_bp.route('/users/<int:user_id>/password', methods=['PUT'])
@require_role('admin')
def change_password(user_id):
    """Reset a staff member's password (admin action)."""
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found', 'message': 'No user found with that ID'}), 404

        data = request.get_json(silent=True) or {}
        password = data.get('password')
        if not password:
            return jsonify({'error': 'missing_required_field', 'message': 'password is required'}), 400

        user.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')
        # Revoke all active sessions for this user — matches /auth/change-password
        # behavior. Atomic: both password update and token revocation commit together.
        SessionToken.query.filter(
            SessionToken.staff_id == user_id,
            SessionToken.is_active == True,
        ).update({'is_active': False})
        _audit('admin_bp.password_change',
               f'Password changed for operator_id={user_id}; all sessions revoked',
               staff_id=user_id)
        db.session.commit()
        return jsonify({'message': 'password updated'}), 200
    except Exception as exc:
        db.session.rollback()
        log_error('admin.change_password', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to change password'}), 500


# ── PUT /v1/users/<id>/status ─────────────────────────────────────

@admin_bp.route('/users/<int:user_id>/status', methods=['PUT'])
@require_role('admin')
def change_status(user_id):
    """Activate or deactivate a staff account."""
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found', 'message': 'No user found with that ID'}), 404

        data = request.get_json(silent=True) or {}
        status_val = data.get('status')
        if not status_val:
            return jsonify({'error': 'missing_required_field', 'message': 'status is required'}), 400
        if status_val not in ('active', 'deactivated'):
            return jsonify({'error': 'invalid_status', 'message': 'status must be active or deactivated'}), 400

        # Cannot deactivate yourself
        if status_val == 'deactivated' and g.current_user.operator_id == user_id:
            return jsonify({'error': 'cannot_deactivate_self', 'message': 'You cannot deactivate your own account'}), 403

        user.is_active = (status_val == 'active')
        _audit('admin_bp.status_change',
               f'Set status={status_val} for operator_id={user_id}',
               staff_id=user_id)
        db.session.commit()
        return jsonify(_user_json(user)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('admin.change_status', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to change status'}), 500


# ── GET /v1/admin/history ─────────────────────────────────────────

@admin_bp.route('/admin/history', methods=['GET'])
@require_role('admin')
def get_history():
    """Query the audit log with optional filters and pagination.

    Query params:
      userId  — filter by staff ID
      action  — substring match on source field
      from    — ISO 8601 lower bound on created_at
      to      — ISO 8601 upper bound on created_at
      page    — 1-based page number (default 1)
      limit   — results per page (default 50, max 200)
    """
    try:
        q = SystemEvent.query

        user_id_param = request.args.get('userId')
        if user_id_param:
            user_id_int = safe_int(user_id_param, 'userId')
            if user_id_int is None:
                return jsonify({'error': 'invalid_user_id', 'message': 'userId must be an integer'}), 400
            q = q.filter(SystemEvent.staff_id == user_id_int)

        action = request.args.get('action')
        if action:
            q = q.filter(SystemEvent.source.contains(action))

        from_date = request.args.get('from')
        if from_date:
            try:
                dt = datetime.fromisoformat(from_date)
            except ValueError:
                return jsonify({'error': 'invalid_date_format', 'message': 'from must be a valid ISO 8601 datetime'}), 400
            q = q.filter(SystemEvent.created_at >= dt)

        to_date = request.args.get('to')
        if to_date:
            try:
                dt = datetime.fromisoformat(to_date)
            except ValueError:
                return jsonify({'error': 'invalid_date_format', 'message': 'to must be a valid ISO 8601 datetime'}), 400
            q = q.filter(SystemEvent.created_at <= dt)

        # Pagination
        page = max(int(request.args.get('page', 1)), 1)
        limit = min(int(request.args.get('limit', 50)), 200)

        total = q.count()
        events = (q.order_by(SystemEvent.event_id.desc())
                   .offset((page - 1) * limit)
                   .limit(limit)
                   .all())

        return jsonify({
            'events': [_event_json(e) for e in events],
            'page': page,
            'limit': limit,
            'total': total,
        }), 200
    except Exception as exc:
        log_error('admin.get_history', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch history'}), 500


# ── Shared report helpers ────────────────────────────────────────

def _parse_report_range():
    """Parse start/end query params for report endpoints."""
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    if not start_str or not end_str:
        missing = 'start' if not start_str else 'end'
        return None, None, (jsonify({'error': 'missing_required_field',
                                     'message': f'{missing} query parameter is required'}), 400)
    try:
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None, None, (jsonify({'error': 'invalid_parameter',
                                     'message': 'start and end must be valid ISO 8601 datetimes'}), 400)
    return start_dt, end_dt, None


def _csv_response(output, filename):
    """Wrap a StringIO CSV buffer in a Flask Response with download headers."""
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── GET /v1/admin/reports/revenue ────────────────────────────────

@admin_bp.route('/admin/reports/revenue', methods=['GET'])
@require_role('admin')
def export_revenue():
    """Export revenue data as CSV, filterable by date range and garage_id.

    Query params:
      start     (required)  ISO 8601
      end       (required)  ISO 8601
      garage_id (optional)  integer — restrict to a single garage
    """
    from models import Payment, PaymentStatusEnum, Ticket, ParkingSpot, Floor

    start_dt, end_dt, err = _parse_report_range()
    if err:
        return err

    try:
        q = Payment.query.filter(
            Payment.payment_timestamp >= start_dt,
            Payment.payment_timestamp <= end_dt,
        )
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            q = (q.join(Ticket, Payment.ticket_id == Ticket.ticket_id)
                   .join(ParkingSpot, Ticket.spot_id == ParkingSpot.spot_id)
                   .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
                   .filter(Floor.garage_id == garage_id))

        payments = q.order_by(Payment.payment_timestamp).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['payment_id', 'ticket_id', 'amount_charged', 'payment_method',
                         'payment_status', 'payment_timestamp'])
        for p in payments:
            writer.writerow([
                p.payment_id,
                p.ticket_id,
                float(p.amount_charged),
                p.payment_method.value,
                p.payment_status.value,
                p.payment_timestamp.isoformat() + 'Z',
            ])

        return _csv_response(output, f'revenue_{start_dt.date()}_{end_dt.date()}.csv')
    except Exception as exc:
        log_error('admin.export_revenue', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to export revenue'}), 500


# ── GET /v1/admin/reports/utilization ────────────────────────────

@admin_bp.route('/admin/reports/utilization', methods=['GET'])
@require_role('admin')
def export_utilization():
    """Export utilization data as CSV, filterable by date range and garage_id.

    Query params:
      start     (required)  ISO 8601
      end       (required)  ISO 8601
      garage_id (optional)  integer — restrict to a single garage
    """
    from sqlalchemy import case, func
    from models import OccupancyLog, OccupancyChangeEnum, ParkingSpot, Floor

    start_dt, end_dt, err = _parse_report_range()
    if err:
        return err

    try:
        garage_id = request.args.get('garage_id', type=int)

        # Total spots for denominator
        total_q = db.session.query(func.count(ParkingSpot.spot_id))
        if garage_id is not None:
            total_q = total_q.join(Floor, ParkingSpot.floor_id == Floor.floor_id).filter(Floor.garage_id == garage_id)
        total_spots = total_q.scalar() or 0

        # Daily bucketing
        bucket_expr = func.strftime('%Y-%m-%d', OccupancyLog.changed_at)
        q = db.session.query(
            bucket_expr.label('day'),
            func.sum(case(
                (OccupancyLog.change_type == OccupancyChangeEnum.occupied, 1),
                else_=0,
            )).label('entries'),
            func.sum(case(
                (OccupancyLog.change_type == OccupancyChangeEnum.freed, 1),
                else_=0,
            )).label('exits'),
        ).filter(
            OccupancyLog.changed_at >= start_dt,
            OccupancyLog.changed_at <= end_dt,
        )
        if garage_id is not None:
            q = (q.join(ParkingSpot, OccupancyLog.spot_id == ParkingSpot.spot_id)
                   .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
                   .filter(Floor.garage_id == garage_id))

        rows = q.group_by('day').order_by('day').all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['date', 'entries', 'exits', 'net_change', 'total_spots'])
        for day, entries, exits in rows:
            entries = entries or 0
            exits = exits or 0
            writer.writerow([day, entries, exits, entries - exits, total_spots])

        return _csv_response(output, f'utilization_{start_dt.date()}_{end_dt.date()}.csv')
    except Exception as exc:
        log_error('admin.export_utilization', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to export utilization'}), 500
