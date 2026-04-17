"""
tests/test_auth.py — Token-based auth module tests

Covers login, refresh, logout, me, register, change-password,
rate limiting, expired tokens, and auth guards on payments/analytics.

Run:  pytest tests/test_auth.py -v
"""

from datetime import datetime, timedelta

import bcrypt
import pytest

from app import app, db
from tests.conftest import auth_header as _auth_header
from models import Staff, SessionToken, StaffRoleEnum


# ======================================================================
#  Fixtures
# ======================================================================

@pytest.fixture()
def admin_user(client):
    """Seed an admin staff account. Returns dict with username/password."""
    with app.app_context():
        pw = 'adminpass123'
        pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        staff = Staff(
            name='Test Admin',
            username='admin',
            password_hash=pw_hash,
            role=StaffRoleEnum.admin,
            is_super_admin=True,
        )
        db.session.add(staff)
        db.session.commit()
        return {
            'operator_id': staff.operator_id,
            'username': 'admin',
            'password': pw,
        }


@pytest.fixture()
def attendant_user(client):
    """Seed an attendant staff account."""
    with app.app_context():
        pw = 'attend12345'
        pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        staff = Staff(
            name='Test Attendant',
            username='attendant1',
            password_hash=pw_hash,
            role=StaffRoleEnum.attendant,
        )
        db.session.add(staff)
        db.session.commit()
        return {
            'operator_id': staff.operator_id,
            'username': 'attendant1',
            'password': pw,
        }


def _login(client, username, password):
    """Helper: login and return (response, token)."""
    resp = client.post('/v1/auth/login', json={
        'username': username,
        'password': password,
    })
    token = None
    if resp.status_code == 200:
        token = resp.get_json()['token']
    return resp, token



# ======================================================================
#  POST /v1/auth/login
# ======================================================================

class TestLogin:

    def test_happy_path(self, client, admin_user):
        resp, token = _login(client, 'admin', 'adminpass123')
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'token' in body
        assert body['user']['username'] == 'admin'
        assert body['user']['role'] == 'admin'
        assert 'expiresAt' in body

    def test_missing_fields(self, client, admin_user):
        resp = client.post('/v1/auth/login', json={'username': 'admin'})
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_wrong_password(self, client, admin_user):
        resp, _ = _login(client, 'admin', 'wrongpass')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid_credentials'

    def test_unknown_user(self, client, admin_user):
        resp, _ = _login(client, 'nobody', 'whatever1')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid_credentials'

    def test_rate_limiting(self, client, admin_user):
        # Clear any prior DB state
        from app import db
        from models import LoginAttempt
        LoginAttempt.query.delete()
        db.session.commit()

        for _ in range(5):
            _login(client, 'admin', 'wrongpass')

        resp, _ = _login(client, 'admin', 'adminpass123')
        assert resp.status_code == 429
        assert resp.get_json()['error'] == 'rate_limited'

        # Clean up
        LoginAttempt.query.delete()
        db.session.commit()


# ======================================================================
#  POST /v1/auth/refresh
# ======================================================================

class TestRefresh:

    def test_happy_path(self, client, admin_user):
        _, old_token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/refresh', headers=_auth_header(old_token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['token'] != old_token

        # Old token should be invalid now
        resp2 = client.get('/v1/auth/me', headers=_auth_header(old_token))
        assert resp2.status_code == 401

    def test_without_token(self, client, admin_user):
        resp = client.post('/v1/auth/refresh')
        assert resp.status_code == 401


# ======================================================================
#  POST /v1/auth/logout
# ======================================================================

class TestLogout:

    def test_happy_path(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/logout', headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.get_json()['message'] == 'Logged out'

        # Token should now be invalid
        resp2 = client.get('/v1/auth/me', headers=_auth_header(token))
        assert resp2.status_code == 401

    def test_without_token(self, client, admin_user):
        resp = client.post('/v1/auth/logout')
        assert resp.status_code == 401


# ======================================================================
#  GET /v1/auth/me
# ======================================================================

class TestMe:

    def test_happy_path(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.get('/v1/auth/me', headers=_auth_header(token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['username'] == 'admin'
        assert body['name'] == 'Test Admin'
        assert body['role'] == 'admin'
        assert 'operatorId' in body

    def test_without_token(self, client, admin_user):
        resp = client.get('/v1/auth/me')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'unauthorized'

    def test_expired_token(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        # Manually expire the token
        with app.app_context():
            st = SessionToken.query.filter_by(token=token).first()
            st.expires_at = datetime.utcnow() - timedelta(hours=1)
            db.session.commit()

        resp = client.get('/v1/auth/me', headers=_auth_header(token))
        assert resp.status_code == 401


# ======================================================================
#  POST /v1/auth/register
# ======================================================================

class TestRegister:

    def test_admin_creates_attendant(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/register', headers=_auth_header(token), json={
            'username': 'newuser',
            'password': 'securepass1',
            'name': 'New User',
        })
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['user']['username'] == 'newuser'
        assert body['user']['role'] == 'attendant'
        assert 'token' in body

    def test_admin_creates_admin(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/register', headers=_auth_header(token), json={
            'username': 'admin2',
            'password': 'securepass1',
            'name': 'Admin Two',
            'role': 'admin',
        })
        assert resp.status_code == 201
        assert resp.get_json()['user']['role'] == 'admin'

    def test_attendant_rejected(self, client, admin_user, attendant_user):
        _, token = _login(client, 'attendant1', 'attend12345')
        resp = client.post('/v1/auth/register', headers=_auth_header(token), json={
            'username': 'hack',
            'password': 'securepass1',
            'name': 'Hacker',
        })
        assert resp.status_code == 403

    def test_duplicate_username(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/register', headers=_auth_header(token), json={
            'username': 'admin',
            'password': 'securepass1',
            'name': 'Dupe',
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'username_taken'

    def test_weak_password(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/register', headers=_auth_header(token), json={
            'username': 'weakuser',
            'password': 'short',
            'name': 'Weak',
        })
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'weak_password'

    def test_missing_fields(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/register', headers=_auth_header(token), json={
            'username': 'nopass',
        })
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'


# ======================================================================
#  POST /v1/auth/change-password
# ======================================================================

class TestChangePassword:

    def test_happy_path(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/change-password', headers=_auth_header(token), json={
            'currentPassword': 'adminpass123',
            'newPassword': 'newadminpass1',
        })
        assert resp.status_code == 200

        # Can login with new password
        resp2, _ = _login(client, 'admin', 'newadminpass1')
        assert resp2.status_code == 200

        # Old password no longer works
        resp3, _ = _login(client, 'admin', 'adminpass123')
        assert resp3.status_code == 401

    def test_wrong_current_password(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/change-password', headers=_auth_header(token), json={
            'currentPassword': 'wrongpass',
            'newPassword': 'newadminpass1',
        })
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid_credentials'

    def test_weak_new_password(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.post('/v1/auth/change-password', headers=_auth_header(token), json={
            'currentPassword': 'adminpass123',
            'newPassword': 'short',
        })
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'weak_password'

    def test_invalidates_other_tokens(self, client, admin_user):
        # Login twice to get two tokens
        _, token1 = _login(client, 'admin', 'adminpass123')
        _, token2 = _login(client, 'admin', 'adminpass123')

        # Change password using token1
        resp = client.post('/v1/auth/change-password', headers=_auth_header(token1), json={
            'currentPassword': 'adminpass123',
            'newPassword': 'newadminpass1',
        })
        assert resp.status_code == 200

        # token1 (used for change) should still work
        resp1 = client.get('/v1/auth/me', headers=_auth_header(token1))
        assert resp1.status_code == 200

        # token2 should be invalidated
        resp2 = client.get('/v1/auth/me', headers=_auth_header(token2))
        assert resp2.status_code == 401


# ======================================================================
#  Auth guards on protected routes
# ======================================================================

class TestAuthGuards:

    def test_analytics_requires_auth(self, client, admin_user):
        resp = client.get('/v1/analytics/occupancy')
        assert resp.status_code == 401

    def test_analytics_with_token(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.get('/v1/analytics/occupancy', headers=_auth_header(token))
        assert resp.status_code == 200

    def test_payments_requires_auth(self, client, admin_user):
        resp = client.get('/v1/payments?ticketId=1')
        assert resp.status_code == 401

    def test_payments_with_token(self, client, admin_user):
        _, token = _login(client, 'admin', 'adminpass123')
        resp = client.get('/v1/payments?ticketId=1', headers=_auth_header(token))
        # 404 is expected (no payment), but not 401
        assert resp.status_code != 401
