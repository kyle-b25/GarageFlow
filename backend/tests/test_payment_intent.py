"""
tests/test_payment_intent.py — Stripe PaymentIntent creation tests

Covers POST /v1/payments/create-intent:
  - Happy path: correct amount and metadata passed to Stripe
  - Missing ticketId → 400
  - Ticket not found → 404
  - Ticket not closed → 409
  - Ticket with zero/null fee → 400
  - Stripe SDK error → 502
  - Links intent to existing payment record
"""

import math
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
import stripe as _stripe

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, GateEvent, Vehicle, Ticket, Payment,
    SpotTypeEnum, SpotStatusEnum, GateTypeEnum, GateStatusEnum,
    VehicleTypeEnum, TicketStatusEnum, PaymentMethodEnum, PaymentStatusEnum,
)


@pytest.fixture()
def closed_ticket(client):
    """Seed infrastructure + a closed ticket with a $15.00 fee."""
    with app.app_context():
        garage = Garage(name='Payment Garage', total_capacity=5,
                        number_of_floors=1, operating_hours='24/7')
        db.session.add(garage)
        db.session.flush()

        floor = Floor(garage_id=garage.garage_id, floor_number=1,
                      total_spots=5, available_spots=5)
        db.session.add(floor)
        db.session.flush()

        spot = ParkingSpot(floor_id=floor.floor_id, spot_type=SpotTypeEnum.standard,
                           status=SpotStatusEnum.available)
        db.session.add(spot)
        db.session.flush()

        gate = GateEvent(garage_id=garage.garage_id, gate_type=GateTypeEnum.entry,
                         status=GateStatusEnum.open)
        db.session.add(gate)
        db.session.flush()

        vehicle = Vehicle(license_plate='PAY-1234', plate_state='CA',
                          vehicle_type=VehicleTypeEnum.car)
        db.session.add(vehicle)
        db.session.flush()

        entry = datetime.utcnow() - timedelta(hours=3)
        exit_time = datetime.utcnow()
        duration = math.ceil((exit_time - entry).total_seconds() / 60)

        ticket = Ticket(
            vehicle_id=vehicle.vehicle_id,
            spot_id=spot.spot_id,
            entry_gate_id=gate.gate_id,
            entry_timestamp=entry,
            exit_timestamp=exit_time,
            duration=duration,
            total_fee=Decimal('15.00'),
            status=TicketStatusEnum.closed,
        )
        db.session.add(ticket)
        db.session.commit()

        return {'ticket_id': ticket.ticket_id}


class TestCreatePaymentIntent:

    def test_happy_path(self, client, closed_ticket):
        """Stripe receives correct amount (cents) and metadata."""
        mock_intent = MagicMock()
        mock_intent.id = 'pi_test_abc'
        mock_intent.client_secret = 'pi_test_abc_secret_xyz'

        with patch('stripe.PaymentIntent.create', return_value=mock_intent) as mock_create:
            resp = client.post('/v1/payments/create-intent', json={
                'ticketId': closed_ticket['ticket_id'],
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['clientSecret'] == 'pi_test_abc_secret_xyz'
        assert data['paymentIntentId'] == 'pi_test_abc'
        assert data['amount'] == 15.0

        # Verify Stripe was called with correct amount and metadata
        mock_create.assert_called_once_with(
            amount=1500,  # $15.00 → 1500 cents
            currency='usd',
            metadata={'ticket_id': str(closed_ticket['ticket_id'])},
        )

    def test_missing_ticket_id(self, client, closed_ticket):
        resp = client.post('/v1/payments/create-intent', json={})
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_ticket_not_found(self, client, closed_ticket):
        resp = client.post('/v1/payments/create-intent', json={
            'ticketId': 99999,
        })
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'ticket_not_found'

    def test_ticket_not_closed(self, client, closed_ticket):
        with app.app_context():
            ticket = Ticket.query.get(closed_ticket['ticket_id'])
            ticket.status = TicketStatusEnum.active
            db.session.commit()

        resp = client.post('/v1/payments/create-intent', json={
            'ticketId': closed_ticket['ticket_id'],
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'ticket_not_closed'

    def test_ticket_zero_fee(self, client, closed_ticket):
        with app.app_context():
            ticket = Ticket.query.get(closed_ticket['ticket_id'])
            ticket.total_fee = Decimal('0.00')
            db.session.commit()

        resp = client.post('/v1/payments/create-intent', json={
            'ticketId': closed_ticket['ticket_id'],
        })
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'no_fee'

    def test_ticket_null_fee(self, client, closed_ticket):
        with app.app_context():
            ticket = Ticket.query.get(closed_ticket['ticket_id'])
            ticket.total_fee = None
            db.session.commit()

        resp = client.post('/v1/payments/create-intent', json={
            'ticketId': closed_ticket['ticket_id'],
        })
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'no_fee'

    def test_stripe_error(self, client, closed_ticket):
        with patch('stripe.PaymentIntent.create',
                   side_effect=_stripe.error.StripeError('test failure')):
            resp = client.post('/v1/payments/create-intent', json={
                'ticketId': closed_ticket['ticket_id'],
            })

        assert resp.status_code == 502
        assert resp.get_json()['error'] == 'stripe_error'
        assert 'test failure' in resp.get_json()['message']

    def test_links_existing_payment_record(self, client, closed_ticket):
        """If a Payment record already exists, the intent ID gets linked."""
        with app.app_context():
            payment = Payment(
                ticket_id=closed_ticket['ticket_id'],
                amount_charged=Decimal('15.00'),
                payment_method=PaymentMethodEnum.card,
                payment_status=PaymentStatusEnum.pending,
                payment_timestamp=datetime.utcnow(),
            )
            db.session.add(payment)
            db.session.commit()
            payment_id = payment.payment_id

        mock_intent = MagicMock()
        mock_intent.id = 'pi_linked_123'
        mock_intent.client_secret = 'pi_linked_secret'

        with patch('stripe.PaymentIntent.create', return_value=mock_intent):
            resp = client.post('/v1/payments/create-intent', json={
                'ticketId': closed_ticket['ticket_id'],
            })

        assert resp.status_code == 200

        with app.app_context():
            payment = Payment.query.get(payment_id)
            assert payment.stripe_payment_intent_id == 'pi_linked_123'
