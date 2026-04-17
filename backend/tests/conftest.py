"""
tests/conftest.py — Shared test fixtures

Provides the Flask test client, auth token creation, and header helpers
used across all test modules.
"""

import secrets
from datetime import datetime, timedelta

import bcrypt
import pytest

from app import app, db
from models import Staff, SessionToken, StaffRoleEnum


@pytest.fixture()
def client():
    """Test client with in-memory SQLite."""
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SECRET_KEY'] = 'test-secret'

    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def create_staff_token(role='admin'):
    """Create a staff user + active token, return token string."""
    pw_hash = bcrypt.hashpw(b'testpass1', bcrypt.gensalt()).decode()
    staff = Staff(
        name=f'Test {role}',
        username=f'test_{role}_{secrets.token_hex(4)}',
        password_hash=pw_hash,
        role=StaffRoleEnum[role],
        is_super_admin=(role == 'admin'),
    )
    db.session.add(staff)
    db.session.flush()

    token_str = secrets.token_hex(32)
    db.session.add(SessionToken(
        staff_id=staff.operator_id,
        token=token_str,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=8),
        is_active=True,
    ))
    db.session.commit()
    return token_str


@pytest.fixture()
def auth_token(client):
    """Admin Bearer token."""
    with app.app_context():
        return create_staff_token('admin')


@pytest.fixture()
def attendant_token(client):
    """Attendant Bearer token."""
    with app.app_context():
        return create_staff_token('attendant')


def auth_header(token):
    """Return Authorization header dict for Bearer token."""
    return {'Authorization': f'Bearer {token}'}
