"""
auth.py — GarageFlow Authentication

Login, logout, and session-status endpoints.
All routes are public (no authentication required to reach them).

Rate limiting: failed login attempts are tracked per IP in an in-memory dict.
After 5 failures within 60 seconds the IP is locked out until the window
expires. The counter is reset on a successful login.
"""

from datetime import datetime
from flask import Blueprint, request, jsonify, session
import bcrypt

auth_bp = Blueprint('auth', __name__)

# In-memory failed-login tracker: { ip: {'count': int, 'window_start': datetime} }
_failed_attempts: dict = {}
_RATE_LIMIT_MAX    = 5
_RATE_LIMIT_WINDOW = 60  # seconds


def _get_client_ip() -> str:
    """Return the best-effort client IP, honouring X-Forwarded-For if present."""
    return request.headers.get('X-Forwarded-For', request.remote_addr)


def _is_rate_limited(ip: str) -> bool:
    """
    Return True if the IP has exceeded the failed-login threshold within
    the current window. Cleans up the entry if the window has expired.
    """
    now   = datetime.utcnow()
    entry = _failed_attempts.get(ip)
    if not entry:
        return False
    if (now - entry['window_start']).total_seconds() > _RATE_LIMIT_WINDOW:
        del _failed_attempts[ip]
        return False
    return entry['count'] >= _RATE_LIMIT_MAX


def _record_failure(ip: str) -> None:
    """Increment the failed-attempt counter, starting a fresh window if needed."""
    now   = datetime.utcnow()
    entry = _failed_attempts.get(ip)
    if not entry or (now - entry['window_start']).total_seconds() > _RATE_LIMIT_WINDOW:
        _failed_attempts[ip] = {'count': 1, 'window_start': now}
    else:
        _failed_attempts[ip]['count'] += 1


def _clear_failures(ip: str) -> None:
    """Reset the failed-attempt counter after a successful login."""
    _failed_attempts.pop(ip, None)


@auth_bp.route('/v1/auth/login', methods=['POST'])
def login():
    """
    POST /v1/auth/login

    Authenticate a staff member and store their identity in the Flask session.
    Rate-limited to 5 failed attempts per IP per 60-second window.
    Does not reveal whether the username or password was wrong.
    """
    ip = _get_client_ip()

    if _is_rate_limited(ip):
        return jsonify({
            'error':   'rate_limited',
            'message': 'Too many login attempts. Try again later.',
        }), 429

    data     = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({
            'error':   'missing_required_field',
            'message': 'username and password are required',
        }), 400

    from models import Staff
    staff = Staff.query.filter_by(username=username).first()

    if not staff or not bcrypt.checkpw(
        password.encode('utf-8'),
        staff.password_hash.encode('utf-8'),
    ):
        _record_failure(ip)
        return jsonify({'error': 'invalid_credentials', 'message': 'Invalid username or password'}), 401

    _clear_failures(ip)
    session.permanent      = True
    session['operator_id'] = staff.operator_id
    session['username']    = staff.username
    session['role']        = staff.role.value

    return jsonify({
        'message':     'Login successful',
        'operator_id': staff.operator_id,
        'username':    staff.username,
        'role':        staff.role.value,
    }), 200


@auth_bp.route('/v1/auth/logout', methods=['POST'])
def logout():
    """
    POST /v1/auth/logout

    Destroy the current session. Idempotent — always returns 200.
    """
    session.clear()
    return jsonify({'message': 'Logged out'}), 200


@auth_bp.route('/v1/auth/status', methods=['GET'])
def auth_status():
    """
    GET /v1/auth/status

    Return the current authentication state without requiring a valid session.
    """
    if 'operator_id' not in session:
        return jsonify({'authenticated': False}), 200

    return jsonify({
        'authenticated': True,
        'operator_id':   session['operator_id'],
        'username':      session['username'],
        'role':          session['role'],
    }), 200
