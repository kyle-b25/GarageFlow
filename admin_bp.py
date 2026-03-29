"""
admin_bp.py — GarageFlow Admin Management API Blueprint

Staff account CRUD and audit history endpoints.

Design decision — audit log:
  SystemEvent (with the new nullable staff_id FK) is used as the audit log.
  No dedicated model is needed: source + description + staff_id provide
  sufficient filtering (by user, action type, and date range).
"""
from datetime import datetime

import bcrypt
from flask import Blueprint, jsonify, request, g

from app import db
from models import Staff, StaffRoleEnum, SystemEvent
from utils import require_role, safe_int

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
    try:
        users = Staff.query.all()
        return jsonify([_user_json(u) for u in users]), 200
    except Exception:
        return jsonify({'error': 'server_error'}), 500


# ── GET /v1/users/<id> ────────────────────────────────────────────

@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@require_role('admin')
def get_user(user_id):
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found'}), 404
        return jsonify(_user_json(user)), 200
    except Exception:
        return jsonify({'error': 'server_error'}), 500


# ── DELETE /v1/users/<id> ─────────────────────────────────────────

@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_role('super_admin')
def delete_user(user_id):
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found'}), 404

        has_audit = SystemEvent.query.filter(
            db.or_(
                SystemEvent.staff_id == user_id,
                SystemEvent.description.contains(f'operator_id={user_id}'),
            )
        ).first()
        if has_audit:
            return jsonify({'error': 'delete_blocked_audit_history'}), 409

        operator_id = user.operator_id
        username = user.username
        db.session.delete(user)
        _audit('admin_bp.delete',
               f'Deleted staff account operator_id={operator_id} username={username}')
        db.session.commit()
        return jsonify({'message': 'user deleted'}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error'}), 500


# ── PUT /v1/users/<id>/role ───────────────────────────────────────

@admin_bp.route('/users/<int:user_id>/role', methods=['PUT'])
@require_role('admin')
def change_role(user_id):
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found'}), 404

        data = request.get_json(silent=True) or {}
        role_val = data.get('role')
        if not role_val:
            return jsonify({'error': 'missing_required_field'}), 400

        try:
            new_role = StaffRoleEnum(role_val)
        except ValueError:
            return jsonify({'error': 'invalid_role'}), 400

        old_role = user.role.value
        user.role = new_role
        _audit('admin_bp.role_change',
               f'Changed role for operator_id={user_id} from {old_role} to {new_role.value}',
               staff_id=user_id)
        db.session.commit()
        return jsonify(_user_json(user)), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error'}), 500


# ── PUT /v1/users/<id>/password ───────────────────────────────────

@admin_bp.route('/users/<int:user_id>/password', methods=['PUT'])
@require_role('admin')
def change_password(user_id):
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found'}), 404

        data = request.get_json(silent=True) or {}
        password = data.get('password')
        if not password:
            return jsonify({'error': 'missing_required_field'}), 400

        user.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')
        _audit('admin_bp.password_change',
               f'Password changed for operator_id={user_id}',
               staff_id=user_id)
        db.session.commit()
        return jsonify({'message': 'password updated'}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error'}), 500


# ── PUT /v1/users/<id>/status ─────────────────────────────────────

@admin_bp.route('/users/<int:user_id>/status', methods=['PUT'])
@require_role('admin')
def change_status(user_id):
    try:
        user = Staff.query.get(user_id)
        if not user:
            return jsonify({'error': 'user_not_found'}), 404

        data = request.get_json(silent=True) or {}
        status_val = data.get('status')
        if not status_val:
            return jsonify({'error': 'missing_required_field'}), 400
        if status_val not in ('active', 'deactivated'):
            return jsonify({'error': 'invalid_status'}), 400

        # Cannot deactivate yourself
        if status_val == 'deactivated' and g.current_user.operator_id == user_id:
            return jsonify({'error': 'cannot_deactivate_self'}), 403

        user.is_active = (status_val == 'active')
        _audit('admin_bp.status_change',
               f'Set status={status_val} for operator_id={user_id}',
               staff_id=user_id)
        db.session.commit()
        return jsonify(_user_json(user)), 200
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error'}), 500


# ── GET /v1/admin/history ─────────────────────────────────────────

@admin_bp.route('/admin/history', methods=['GET'])
@require_role('admin')
def get_history():
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
                return jsonify({'error': 'invalid_date_format'}), 400
            q = q.filter(SystemEvent.created_at >= dt)

        events = q.order_by(SystemEvent.event_id.desc()).all()
        return jsonify([_event_json(e) for e in events]), 200
    except Exception:
        return jsonify({'error': 'server_error'}), 500
