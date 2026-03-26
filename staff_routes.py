"""
staff_routes.py — GarageFlow Staff Blueprint

All routes registered on this blueprint require an authenticated staff session.
Admin-only routes additionally use the @require_admin decorator.

Exports:
  staff_bp      — Flask Blueprint; register with app.register_blueprint()
  require_admin — decorator for admin-only route functions
"""

from functools import wraps
import bcrypt
from flask import Blueprint, request, jsonify, session

staff_bp    = Blueprint('staff', __name__)
_VALID_ROLES = {'admin', 'attendant'}


# ------------------------------------------------------------------
#  Authentication middleware
# ------------------------------------------------------------------

@staff_bp.before_request
def require_auth():
    """Reject any request that arrives without an active staff session."""
    if 'operator_id' not in session:
        return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401


# ------------------------------------------------------------------
#  Decorators
# ------------------------------------------------------------------

def require_admin(f):
    """
    Route decorator that restricts access to admin-role sessions.
    Apply after @staff_bp.route (i.e., closer to the function definition).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
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
