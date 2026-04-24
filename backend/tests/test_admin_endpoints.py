"""
tests/test_admin_endpoints.py — Admin endpoint tests

Covers:
  GET  /v1/users           — List all staff accounts
  GET  /v1/users/<id>      — Single staff lookup
  GET  /v1/admin/history   — Audit log with filters and pagination

Tests auth enforcement, happy paths, and edge cases.
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import Staff, StaffRoleEnum, SystemEvent
from tests.conftest import create_staff_token, auth_header


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def admin_with_events(client, auth_token):
    """Create audit events for history tests. Returns (token, staff_id)."""
    with app.app_context():
        staff = Staff.query.first()
        staff_id = staff.operator_id
        for i in range(5):
            db.session.add(SystemEvent(
                staff_id=staff_id,
                source=f'test_source_{i}',
                description=f'Test event {i}',
                created_at=datetime.utcnow() - timedelta(hours=i),
            ))
        db.session.commit()
    return auth_token, staff_id


# ======================================================================
#  GET /v1/users
# ======================================================================

class TestListUsers:

    def test_happy_path(self, client, auth_token):
        resp = client.get('/v1/users', headers=auth_header(auth_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert 'operatorId' in data[0]
        assert 'username' in data[0]
        assert 'role' in data[0]

    def test_returns_all_staff(self, client, auth_token, attendant_token):
        """Both admin and attendant should appear in the list."""
        resp = client.get('/v1/users', headers=auth_header(auth_token))
        assert resp.status_code == 200
        data = resp.get_json()
        roles = {u['role'] for u in data}
        assert 'admin' in roles
        assert 'attendant' in roles

    def test_unauthenticated(self, client):
        resp = client.get('/v1/users')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'unauthorized'

    def test_attendant_rejected(self, client, attendant_token):
        resp = client.get('/v1/users', headers=auth_header(attendant_token))
        assert resp.status_code == 403


# ======================================================================
#  GET /v1/users/<id>
# ======================================================================

class TestGetUser:

    def test_happy_path(self, client, auth_token):
        resp = client.get('/v1/users', headers=auth_header(auth_token))
        user_id = resp.get_json()[0]['operatorId']

        resp = client.get(f'/v1/users/{user_id}', headers=auth_header(auth_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['operatorId'] == user_id
        assert 'name' in data
        assert 'isActive' in data

    def test_not_found(self, client, auth_token):
        resp = client.get('/v1/users/99999', headers=auth_header(auth_token))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'user_not_found'

    def test_unauthenticated(self, client):
        resp = client.get('/v1/users/1')
        assert resp.status_code == 401

    def test_attendant_rejected(self, client, attendant_token):
        resp = client.get('/v1/users/1', headers=auth_header(attendant_token))
        assert resp.status_code == 403


# ======================================================================
#  GET /v1/admin/history
# ======================================================================

class TestGetHistory:

    def test_happy_path(self, client, admin_with_events):
        token, _ = admin_with_events
        resp = client.get('/v1/admin/history', headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'events' in data
        assert 'page' in data
        assert 'limit' in data
        assert 'total' in data
        assert data['total'] >= 5

    def test_empty_results(self, client, auth_token):
        """No audit events exist yet — should return empty list."""
        resp = client.get('/v1/admin/history', headers=auth_header(auth_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['events'] == []
        assert data['total'] == 0

    def test_filter_by_user_id(self, client, admin_with_events):
        token, staff_id = admin_with_events
        resp = client.get(f'/v1/admin/history?userId={staff_id}',
                          headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        for event in data['events']:
            assert event['staffId'] == staff_id

    def test_filter_by_action(self, client, admin_with_events):
        token, _ = admin_with_events
        resp = client.get('/v1/admin/history?action=test_source_0',
                          headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 1
        for event in data['events']:
            assert 'test_source_0' in event['source']

    def test_filter_by_date_range(self, client, admin_with_events):
        token, _ = admin_with_events
        now = datetime.utcnow()
        from_dt = (now - timedelta(hours=2)).isoformat()
        to_dt = now.isoformat()

        resp = client.get(f'/v1/admin/history?from={from_dt}&to={to_dt}',
                          headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 2

    def test_pagination(self, client, admin_with_events):
        token, _ = admin_with_events
        resp = client.get('/v1/admin/history?page=1&limit=2',
                          headers=auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['page'] == 1
        assert data['limit'] == 2
        assert len(data['events']) <= 2

        resp = client.get('/v1/admin/history?page=2&limit=2',
                          headers=auth_header(token))
        assert resp.status_code == 200
        data2 = resp.get_json()
        assert data2['page'] == 2

    def test_invalid_user_id(self, client, admin_with_events):
        token, _ = admin_with_events
        resp = client.get('/v1/admin/history?userId=not_a_number',
                          headers=auth_header(token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_user_id'

    def test_invalid_from_date(self, client, admin_with_events):
        token, _ = admin_with_events
        resp = client.get('/v1/admin/history?from=not-a-date',
                          headers=auth_header(token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_date_format'

    def test_invalid_to_date(self, client, admin_with_events):
        token, _ = admin_with_events
        resp = client.get('/v1/admin/history?to=not-a-date',
                          headers=auth_header(token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_date_format'

    def test_unauthenticated(self, client):
        resp = client.get('/v1/admin/history')
        assert resp.status_code == 401

    def test_attendant_rejected(self, client, attendant_token):
        resp = client.get('/v1/admin/history', headers=auth_header(attendant_token))
        assert resp.status_code == 403
