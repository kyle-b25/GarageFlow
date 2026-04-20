"""
routes/auth.py — GarageFlow Token-Based Authentication

Endpoints:
  POST /v1/auth/login           — Authenticate, return Bearer token
  POST /v1/auth/refresh         — Exchange current token for a new one
  POST /v1/auth/logout          — Revoke current token
  GET  /v1/auth/me              — Current user info
  POST /v1/auth/register        — Admin-only: create new staff account
  POST /v1/auth/change-password — Change own password, revoke other tokens
"""

import secrets
from datetime import datetime, timedelta

import bcrypt
from flask import Blueprint, request, jsonify, g

from utils import login_required, require_role

token_auth_bp = Blueprint('token_auth', __name__, url_prefix='/v1/auth')

_TOKEN_TTL_HOURS = 8

# DB-backed rate limiting — survives worker restarts, works across processes.
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60  # seconds


def _get_client_ip() -> str:
    return request.headers.get('X-Forwarded-For', request.remote_addr)


def _is_rate_limited(ip: str) -> bool:
    from app import db
    from models import LoginAttempt
    now = datetime.utcnow()
    entry = LoginAttempt.query.get(ip)
    if not entry:
        return False
    if (now - entry.window_start).total_seconds() > _RATE_LIMIT_WINDOW:
        db.session.delete(entry)
        db.session.commit()
        return False
    return entry.fail_count >= _RATE_LIMIT_MAX


def _record_failure(ip: str) -> None:
    from app import db
    from models import LoginAttempt
    now = datetime.utcnow()
    entry = LoginAttempt.query.get(ip)
    if not entry or (now - entry.window_start).total_seconds() > _RATE_LIMIT_WINDOW:
        if entry:
            db.session.delete(entry)
        db.session.add(LoginAttempt(ip_address=ip, fail_count=1, window_start=now))
    else:
        entry.fail_count += 1
    db.session.commit()


def _clear_failures(ip: str) -> None:
    from app import db
    from models import LoginAttempt
    entry = LoginAttempt.query.get(ip)
    if entry:
        db.session.delete(entry)
        db.session.commit()


def _create_token(staff_id):
    """Create a new SessionToken row and return it."""
    from app import db
    from models import SessionToken

    now = datetime.utcnow()
    token_obj = SessionToken(
        staff_id=staff_id,
        token=secrets.token_hex(32),
        created_at=now,
        expires_at=now + timedelta(hours=_TOKEN_TTL_HOURS),
        is_active=True,
    )
    db.session.add(token_obj)
    return token_obj


def _token_response(token_obj, staff):
    return {
        'token': token_obj.token,
        'expiresAt': token_obj.expires_at.isoformat() + 'Z',
        'user': {
            'operatorId': staff.operator_id,
            'username': staff.username,
            'role': staff.role.value,
        },
    }


# ------------------------------------------------------------------
#  POST /v1/auth/login
# ------------------------------------------------------------------

@token_auth_bp.route('/login', methods=['POST'])
def login():
    """Authenticate with username/password and return a Bearer token."""
    from app import db
    from models import Staff

    ip = _get_client_ip()
    if _is_rate_limited(ip):
        return jsonify({
            'error': 'rate_limited',
            'message': 'Too many login attempts. Try again later.',
        }), 429

    data = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({
            'error': 'missing_required_field',
            'message': 'username and password are required',
        }), 400

    staff = Staff.query.filter_by(username=username).first()
    if not staff or not bcrypt.checkpw(
        password.encode('utf-8'),
        staff.password_hash.encode('utf-8'),
    ):
        _record_failure(ip)
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'Invalid username or password',
        }), 401

    _clear_failures(ip)
    token_obj = _create_token(staff.operator_id)
    db.session.commit()

    return jsonify(_token_response(token_obj, staff)), 200


# ------------------------------------------------------------------
#  POST /v1/auth/refresh
# ------------------------------------------------------------------

@token_auth_bp.route('/refresh', methods=['POST'])
@login_required
def refresh():
    """Exchange the current token for a new one."""
    from app import db

    g.session_token.is_active = False
    new_token_obj = _create_token(g.current_user.operator_id)
    db.session.commit()

    return jsonify(_token_response(new_token_obj, g.current_user)), 200


# ------------------------------------------------------------------
#  POST /v1/auth/logout
# ------------------------------------------------------------------

@token_auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """Revoke the current token."""
    from app import db

    g.session_token.is_active = False
    db.session.commit()

    return jsonify({'message': 'Logged out'}), 200


# ------------------------------------------------------------------
#  GET /v1/auth/me
# ------------------------------------------------------------------

@token_auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    """Return the current authenticated user's profile."""
    user = g.current_user
    return jsonify({
        'operatorId': user.operator_id,
        'username': user.username,
        'name': user.name,
        'role': user.role.value,
    }), 200


# ------------------------------------------------------------------
#  POST /v1/auth/register
# ------------------------------------------------------------------

@token_auth_bp.route('/register', methods=['POST'])
@require_role('admin')
def register():
    """Admin-only: create a new staff account and return a token."""
    from app import db
    from models import Staff, StaffRoleEnum

    data = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')
    name = data.get('name')
    role = data.get('role', 'attendant')

    if not username or not password or not name:
        missing = 'username' if not username else ('password' if not password else 'name')
        return jsonify({
            'error': 'missing_required_field',
            'message': f'{missing} is required',
        }), 400

    if len(password) < 8:
        return jsonify({
            'error': 'weak_password',
            'message': 'Password must be at least 8 characters',
        }), 400

    if role not in StaffRoleEnum.__members__:
        return jsonify({
            'error': 'invalid_parameter',
            'message': f'role must be one of: {", ".join(sorted(StaffRoleEnum.__members__))}',
        }), 400

    if Staff.query.filter_by(username=username).first():
        return jsonify({
            'error': 'username_taken',
            'message': 'Username is already in use',
        }), 409

    password_hash = bcrypt.hashpw(
        password.encode('utf-8'), bcrypt.gensalt()
    ).decode('utf-8')

    staff = Staff(
        name=name,
        username=username,
        password_hash=password_hash,
        role=StaffRoleEnum[role],
    )
    db.session.add(staff)
    db.session.flush()

    token_obj = _create_token(staff.operator_id)
    db.session.commit()

    return jsonify(_token_response(token_obj, staff)), 201


# ------------------------------------------------------------------
#  POST /v1/auth/change-password
# ------------------------------------------------------------------

@token_auth_bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change the current user's password and revoke other tokens."""
    from app import db
    from models import SessionToken

    data = request.get_json(silent=True) or {}
    current_password = data.get('currentPassword')
    new_password = data.get('newPassword')

    if not current_password or not new_password:
        missing = 'currentPassword' if not current_password else 'newPassword'
        return jsonify({
            'error': 'missing_required_field',
            'message': f'{missing} is required',
        }), 400

    if len(new_password) < 8:
        return jsonify({
            'error': 'weak_password',
            'message': 'Password must be at least 8 characters',
        }), 400

    user = g.current_user
    if not bcrypt.checkpw(
        current_password.encode('utf-8'),
        user.password_hash.encode('utf-8'),
    ):
        return jsonify({
            'error': 'invalid_credentials',
            'message': 'Current password is incorrect',
        }), 401

    user.password_hash = bcrypt.hashpw(
        new_password.encode('utf-8'), bcrypt.gensalt()
    ).decode('utf-8')

    SessionToken.query.filter(
        SessionToken.staff_id == user.operator_id,
        SessionToken.id != g.session_token.id,
        SessionToken.is_active == True,
    ).update({'is_active': False})

    db.session.commit()

    return jsonify({'message': 'Password changed successfully'}), 200
