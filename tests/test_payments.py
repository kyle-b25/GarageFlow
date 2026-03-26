"""
tests/test_payments.py — Payment module tests

Covers all seven endpoints with happy paths and error paths.
All Stripe calls are mocked — no real API traffic.

Run:  pytest tests/test_payments.py -v
"""

import json
import math
import secrets
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import bcrypt
import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, Vehicle, Ticket, Payment, Staff, SessionToken,
    SpotTypeEnum, SpotStatusEnum, VehicleTypeEnum,
    TicketStatusEnum, PaymentMethodEnum, PaymentStatusEnum, StaffRoleEnum,
)


@pytest.fixture()
def client():
    """Create a test client with an in-memory SQLite database."""
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SECRET_KEY'] = 'test-secret'

    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _create_staff_token(role='admin'):
    """Create a staff user + active token, return token string."""
    pw_hash = bcrypt.hashpw(b'testpass1', bcrypt.gensalt()).decode()
    staff = Staff(
        name=f'Test {role}',
        username=f'test_{role}_{secrets.token_hex(4)}',
        password_hash=pw_hash,
        role=StaffRoleEnum[role],
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
        return _create_staff_token('admin')


@pytest.fixture()
def attendant_token(client):
    """Attendant Bearer token."""
    with app.app_context():
        return _create_staff_token('attendant')


@pytest.fixture()
def seed_data(client):
    """Seed a garage, floor, spot, vehicle, and closed ticket."""
    with app.app_context():
        garage = Garage(name='Test Garage', total_capacity=10, number_of_floors=1)
        db.session.add(garage)
        db.session.flush()

        floor = Floor(garage_id=garage.garage_id, floor_number=1,
                      floor_name='Ground', total_spots=5, available_spots=5)
        db.session.add(floor)
        db.session.flush()

        spot = ParkingSpot(floor_id=floor.floor_id, spot_type=SpotTypeEnum.standard,
                           status=SpotStatusEnum.available, location_reference='A-01')
        db.session.add(spot)
        db.session.flush()

        vehicle = Vehicle(license_plate='TEST123', vehicle_type=VehicleTypeEnum.car)
        db.session.add(vehicle)
        db.session.flush()

        entry_time = datetime.utcnow() - timedelta(hours=2)
        exit_time = datetime.utcnow()
        duration = math.ceil((exit_time - entry_time).total_seconds() / 60)
        fee = Decimal('5.00') + Decimal('2.00') * math.ceil(duration / 60)

        ticket = Ticket(
            vehicle_id=vehicle.vehicle_id,
            spot_id=spot.spot_id,
            entry_timestamp=entry_time,
            exit_timestamp=exit_time,
            duration=duration,
            total_fee=fee,
            status=TicketStatusEnum.closed,
        )
        db.session.add(ticket)
        db.session.commit()

        return {
            'ticket_id': ticket.ticket_id,
            'vehicle_id': vehicle.vehicle_id,
            'spot_id': spot.spot_id,
        }


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _make_payment(client, seed_data, token, intent_id='pi_test_123'):
    """Helper: create a payment via POST and return response."""
    mock_intent = MagicMock()
    mock_intent.amount_received = 900
    mock_intent.status = 'succeeded'

    with patch('stripe.PaymentIntent.retrieve', return_value=mock_intent):
        return client.post('/v1/payments', json={
            'ticketId': seed_data['ticket_id'],
            'paymentIntentId': intent_id,
        }, headers=_auth(token))


# ======================================================================
#  POST /v1/payments
# ======================================================================

class TestCreatePayment:

    def test_happy_path(self, client, seed_data, auth_token):
        resp = _make_payment(client, seed_data, auth_token)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['ticketId'] == seed_data['ticket_id']
        assert body['amountCharged'] == 9.0
        assert body['paymentStatus'] == 'paid'
        assert body['stripePaymentIntentId'] == 'pi_test_123'

    def test_missing_ticket_id(self, client, seed_data, auth_token):
        resp = client.post('/v1/payments', json={'paymentIntentId': 'pi_x'},
                           headers=_auth(auth_token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_missing_intent_id(self, client, seed_data, auth_token):
        resp = client.post('/v1/payments', json={'ticketId': seed_data['ticket_id']},
                           headers=_auth(auth_token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_ticket_not_found(self, client, seed_data, auth_token):
        resp = client.post('/v1/payments', json={
            'ticketId': 9999, 'paymentIntentId': 'pi_x',
        }, headers=_auth(auth_token))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'ticket_not_found'

    def test_ticket_not_closed(self, client, seed_data, auth_token):
        with app.app_context():
            ticket = Ticket.query.get(seed_data['ticket_id'])
            ticket.status = TicketStatusEnum.active
            db.session.commit()

        resp = client.post('/v1/payments', json={
            'ticketId': seed_data['ticket_id'],
            'paymentIntentId': 'pi_x',
        }, headers=_auth(auth_token))
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'ticket_not_closed'

    def test_duplicate_payment(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        mock_intent = MagicMock()
        mock_intent.amount_received = 900
        mock_intent.status = 'succeeded'

        with patch('stripe.PaymentIntent.retrieve', return_value=mock_intent):
            resp = client.post('/v1/payments', json={
                'ticketId': seed_data['ticket_id'],
                'paymentIntentId': 'pi_dup',
            }, headers=_auth(auth_token))
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'duplicate_payment'

    def test_stripe_retrieve_error(self, client, seed_data, auth_token):
        import stripe as _stripe
        with patch('stripe.PaymentIntent.retrieve',
                   side_effect=_stripe.error.StripeError('bad')):
            resp = client.post('/v1/payments', json={
                'ticketId': seed_data['ticket_id'],
                'paymentIntentId': 'pi_bad',
            }, headers=_auth(auth_token))
        assert resp.status_code == 502
        assert resp.get_json()['error'] == 'stripe_error'


# ======================================================================
#  GET /v1/payments/<id>
# ======================================================================

class TestGetPayment:

    def test_happy_path(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            payment = Payment.query.first()
            pid = payment.payment_id

        resp = client.get(f'/v1/payments/{pid}', headers=_auth(auth_token))
        assert resp.status_code == 200
        assert resp.get_json()['paymentId'] == pid

    def test_not_found(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments/9999', headers=_auth(auth_token))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'payment_not_found'


# ======================================================================
#  GET /v1/payments?ticketId= / ?plate=
# ======================================================================

class TestListPayments:

    def test_by_ticket_id(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        resp = client.get(f'/v1/payments?ticketId={seed_data["ticket_id"]}',
                          headers=_auth(auth_token))
        assert resp.status_code == 200
        assert resp.get_json()['ticketId'] == seed_data['ticket_id']

    def test_by_plate(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        resp = client.get('/v1/payments?plate=TEST123', headers=_auth(auth_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 1

    def test_by_plate_case_insensitive(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        resp = client.get('/v1/payments?plate=test123', headers=_auth(auth_token))
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_by_plate_not_found(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments?plate=UNKNOWN', headers=_auth(auth_token))
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_missing_params(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments', headers=_auth(auth_token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_ticket_id_not_found(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments?ticketId=9999', headers=_auth(auth_token))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'payment_not_found'


# ======================================================================
#  POST /v1/payments/<id>/refund
# ======================================================================

class TestRefundPayment:

    def test_full_refund(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        with patch('stripe.Refund.create') as mock_refund:
            resp = client.post(f'/v1/payments/{pid}/refund', headers=_auth(auth_token))

        assert resp.status_code == 200
        assert resp.get_json()['paymentStatus'] == 'refunded'
        mock_refund.assert_called_once_with(payment_intent='pi_test_123')

    def test_partial_refund(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        with patch('stripe.Refund.create') as mock_refund:
            resp = client.post(f'/v1/payments/{pid}/refund', json={'amount': 3.50},
                               headers=_auth(auth_token))

        assert resp.status_code == 200
        mock_refund.assert_called_once_with(payment_intent='pi_test_123', amount=350)

    def test_already_refunded(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            payment = Payment.query.first()
            payment.payment_status = PaymentStatusEnum.refunded
            db.session.commit()
            pid = payment.payment_id

        resp = client.post(f'/v1/payments/{pid}/refund', headers=_auth(auth_token))
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'already_refunded'

    def test_not_found(self, client, seed_data, auth_token):
        resp = client.post('/v1/payments/9999/refund', headers=_auth(auth_token))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'payment_not_found'

    def test_stripe_refund_error(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        import stripe as _stripe
        with patch('stripe.Refund.create',
                   side_effect=_stripe.error.StripeError('refund failed')):
            resp = client.post(f'/v1/payments/{pid}/refund', headers=_auth(auth_token))

        assert resp.status_code == 502
        assert resp.get_json()['error'] == 'stripe_error'


# ======================================================================
#  GET /v1/payments/reports
# ======================================================================

class TestPaymentReports:

    def test_happy_path(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        start = (datetime.utcnow() - timedelta(days=1)).isoformat() + 'Z'
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + 'Z'

        resp = client.get(f'/v1/payments/reports?start={start}&end={end}',
                          headers=_auth(auth_token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['totalPayments'] == 1
        assert body['paidCount'] == 1
        assert body['totalRevenue'] == 9.0

    def test_empty_range(self, client, seed_data, auth_token):
        start = '2000-01-01T00:00:00Z'
        end = '2000-01-02T00:00:00Z'
        resp = client.get(f'/v1/payments/reports?start={start}&end={end}',
                          headers=_auth(auth_token))
        assert resp.status_code == 200
        assert resp.get_json()['totalPayments'] == 0

    def test_missing_start(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments/reports?end=2030-01-01T00:00:00Z',
                          headers=_auth(auth_token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_missing_end(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments/reports?start=2020-01-01T00:00:00Z',
                          headers=_auth(auth_token))
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_invalid_date_format(self, client, seed_data, auth_token):
        resp = client.get('/v1/payments/reports?start=not-a-date&end=also-bad',
                          headers=_auth(auth_token))
        assert resp.status_code == 400

    def test_unauthenticated(self, client, seed_data):
        start = '2020-01-01T00:00:00Z'
        end = '2030-01-01T00:00:00Z'
        resp = client.get(f'/v1/payments/reports?start={start}&end={end}')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'unauthorized'


# ======================================================================
#  POST /v1/payments/<id>/override
# ======================================================================

class TestOverridePayment:

    def test_happy_path(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        resp = client.post(f'/v1/payments/{pid}/override', json={
            'amountCharged': 15.00,
            'paymentStatus': 'pending',
        }, headers=_auth(auth_token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['amountCharged'] == 15.0
        assert body['paymentStatus'] == 'pending'

    def test_unauthenticated(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        resp = client.post(f'/v1/payments/{pid}/override', json={
            'amountCharged': 1.00,
        })
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'unauthorized'

    def test_attendant_rejected(self, client, seed_data, auth_token, attendant_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        resp = client.post(f'/v1/payments/{pid}/override', json={
            'amountCharged': 1.00,
        }, headers=_auth(attendant_token))
        assert resp.status_code == 403

    def test_not_found(self, client, seed_data, auth_token):
        resp = client.post('/v1/payments/9999/override', json={
            'amountCharged': 1.00,
        }, headers=_auth(auth_token))
        assert resp.status_code == 404

    def test_no_fields(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        resp = client.post(f'/v1/payments/{pid}/override', json={},
                           headers=_auth(auth_token))
        assert resp.status_code == 400

    def test_invalid_status(self, client, seed_data, auth_token):
        _make_payment(client, seed_data, auth_token)
        with app.app_context():
            pid = Payment.query.first().payment_id

        resp = client.post(f'/v1/payments/{pid}/override', json={
            'paymentStatus': 'nonexistent',
        }, headers=_auth(auth_token))
        assert resp.status_code == 400
