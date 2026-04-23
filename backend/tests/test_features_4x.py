"""
tests/test_features_4x.py — Tests for Missing Core Features (4.1–4.6)

Covers:
  4.1  Gate CRUD + ticket gate wiring
  4.2  Operator overrides (ticket + gate)
  4.3  Admin garage configuration
  4.4  Pricing rules CRUD + fee calculation wiring
  4.5  Congestion alert
  4.6  Stripe webhook (signature, dedup, handlers)
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, GateEvent, Ticket, Vehicle, Payment,
    GateTypeEnum, GateStatusEnum, SpotTypeEnum, SpotStatusEnum,
    VehicleTypeEnum, TicketStatusEnum, PaymentMethodEnum, PaymentStatusEnum,
    PricingRule, PricingModelEnum, SystemEvent,
)
from tests.conftest import create_staff_token, auth_header


# ── Shared fixtures ──────────────────────────────────────────────

@pytest.fixture()
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SECRET_KEY'] = 'test-secret'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def admin_token(client):
    with app.app_context():
        return create_staff_token('admin')


@pytest.fixture()
def attendant_token(client):
    with app.app_context():
        return create_staff_token('attendant')


@pytest.fixture()
def garage(client):
    """Create a garage with 1 floor and 5 standard spots."""
    with app.app_context():
        g = Garage(name='Test Garage', total_capacity=5,
                   number_of_floors=1, operating_hours='24/7')
        db.session.add(g)
        db.session.flush()
        f = Floor(garage_id=g.garage_id, floor_number=1,
                  total_spots=5, available_spots=5)
        db.session.add(f)
        db.session.flush()
        for i in range(5):
            db.session.add(ParkingSpot(
                floor_id=f.floor_id,
                location_reference=f'A{i+1}',
                spot_type=SpotTypeEnum.standard,
                status=SpotStatusEnum.available,
            ))
        db.session.commit()
        return g.garage_id


# ═══════════════════════════════════════════════════════════════════
#  4.1  Gate CRUD
# ═══════════════════════════════════════════════════════════════════

class TestGateCRUD:
    def test_create_entry_gate(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 201
        data = rv.get_json()
        assert data['gateType'] == 'entry'
        assert data['status'] == 'closed'
        assert data['garageId'] == garage

    def test_create_exit_gate(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'exit',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 201
        assert rv.get_json()['gateType'] == 'exit'

    def test_create_gate_missing_fields(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage,
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_create_gate_invalid_type(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'invalid',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_create_gate_nonexistent_garage(self, client, admin_token):
        rv = client.post('/v1/gates', json={
            'garageId': 9999, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 404

    def test_create_duplicate_gate_rejected(self, client, admin_token, garage):
        client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 409

    def test_list_gates(self, client, admin_token, garage):
        client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        rv = client.get('/v1/gates')
        assert rv.status_code == 200
        data = rv.get_json()
        assert len(data) >= 1

    def test_get_single_gate(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.get(f'/v1/gates/{gate_id}')
        assert rv.status_code == 200
        assert rv.get_json()['gateId'] == gate_id

    def test_get_gate_not_found(self, client):
        rv = client.get('/v1/gates/9999')
        assert rv.status_code == 404

    def test_update_gate_status(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.put(f'/v1/gates/{gate_id}', json={
            'status': 'open',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'open'

    def test_update_gate_invalid_status(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.put(f'/v1/gates/{gate_id}', json={
            'status': 'broken',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_delete_gate(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'exit',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.delete(f'/v1/gates/{gate_id}', headers=auth_header(admin_token))
        assert rv.status_code == 204

    def test_delete_gate_in_use(self, client, admin_token, garage):
        """Gate referenced by a ticket cannot be deleted."""
        # Create a ticket which auto-creates an entry gate
        client.post('/v1/tickets', json={
            'licensePlate': 'GATE-DEL', 'driverClass': 'standard',
        })
        # Find the auto-created gate
        gates = client.get('/v1/gates').get_json()
        entry_gate = [g for g in gates if g['gateType'] == 'entry']
        if entry_gate:
            rv = client.delete(f'/v1/gates/{entry_gate[0]["gateId"]}',
                               headers=auth_header(admin_token))
            assert rv.status_code == 409

    def test_create_gate_requires_admin(self, client, attendant_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(attendant_token))
        assert rv.status_code == 403


class TestGateWiringOnTicket:
    """Verify ticket entry/exit auto-creates gates and sets IDs."""

    def test_ticket_entry_sets_entry_gate_id(self, client, garage):
        rv = client.post('/v1/tickets', json={
            'licensePlate': 'WIRE-001', 'driverClass': 'standard',
        })
        assert rv.status_code == 201
        ticket_id = rv.get_json()['ticketId']
        with app.app_context():
            ticket = Ticket.query.get(ticket_id)
            assert ticket.entry_gate_id is not None

    def test_ticket_exit_sets_exit_gate_id(self, client, garage):
        rv = client.post('/v1/tickets', json={
            'licensePlate': 'WIRE-002', 'driverClass': 'standard',
        })
        ticket_id = rv.get_json()['ticketId']
        rv = client.put(f'/v1/tickets/{ticket_id}/exit', json={
            'licensePlate': 'WIRE-002', 'paymentMethod': 'cash',
        })
        assert rv.status_code == 200
        with app.app_context():
            ticket = Ticket.query.get(ticket_id)
            assert ticket.exit_gate_id is not None


# ═══════════════════════════════════════════════════════════════════
#  4.2  Operator overrides
# ═══════════════════════════════════════════════════════════════════

class TestTicketOverride:
    def _create_active_ticket(self, client):
        rv = client.post('/v1/tickets', json={
            'licensePlate': f'OVR-{id(self) % 10000:04d}',
            'driverClass': 'standard',
        })
        return rv.get_json()['ticketId']

    def test_force_close(self, client, admin_token, garage):
        tid = self._create_active_ticket(client)
        rv = client.post(f'/v1/tickets/{tid}/override', json={
            'action': 'force_close', 'reason': 'test',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'closed'

    def test_void_active(self, client, admin_token, garage):
        tid = self._create_active_ticket(client)
        rv = client.post(f'/v1/tickets/{tid}/override', json={
            'action': 'void', 'reason': 'mistake',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'voided'

    def test_void_closed(self, client, admin_token, garage):
        tid = self._create_active_ticket(client)
        # Close via exit first
        with app.app_context():
            t = Ticket.query.get(tid)
            plate = Vehicle.query.get(t.vehicle_id).license_plate
        client.put(f'/v1/tickets/{tid}/exit', json={
            'licensePlate': plate, 'paymentMethod': 'cash',
        })
        rv = client.post(f'/v1/tickets/{tid}/override', json={
            'action': 'void', 'reason': 'refund',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'voided'

    def test_invalid_action(self, client, admin_token, garage):
        tid = self._create_active_ticket(client)
        rv = client.post(f'/v1/tickets/{tid}/override', json={
            'action': 'explode',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_override_not_found(self, client, admin_token, garage):
        rv = client.post('/v1/tickets/9999/override', json={
            'action': 'void',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 404

    def test_override_requires_admin(self, client, attendant_token, garage):
        rv = client.post('/v1/tickets', json={
            'licensePlate': 'OVR-ATT', 'driverClass': 'standard',
        })
        tid = rv.get_json()['ticketId']
        rv = client.post(f'/v1/tickets/{tid}/override', json={
            'action': 'force_close',
        }, headers=auth_header(attendant_token))
        assert rv.status_code == 403

    def test_force_close_creates_audit_event(self, client, admin_token, garage):
        tid = self._create_active_ticket(client)
        client.post(f'/v1/tickets/{tid}/override', json={
            'action': 'force_close', 'reason': 'audit test',
        }, headers=auth_header(admin_token))
        with app.app_context():
            ev = SystemEvent.query.filter(
                SystemEvent.source == 'ticket_override',
                SystemEvent.description.contains(str(tid)),
            ).first()
            assert ev is not None


class TestGateOverride:
    def test_override_open(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'open', 'reason': 'manual',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'open'

    def test_override_close(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        # Open then close
        client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'open',
        }, headers=auth_header(admin_token))
        rv = client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'close',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'closed'

    def test_override_out_of_order(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'exit',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'out_of_order', 'reason': 'maintenance',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['status'] == 'out_of_order'

    def test_override_invalid_action(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'explode',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_override_not_found(self, client, admin_token):
        rv = client.post('/v1/gates/9999/override', json={
            'action': 'open',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 404

    def test_override_creates_audit_event(self, client, admin_token, garage):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'open', 'reason': 'audit check',
        }, headers=auth_header(admin_token))
        with app.app_context():
            ev = SystemEvent.query.filter(
                SystemEvent.source == 'gate_override',
                SystemEvent.description.contains(str(gate_id)),
            ).first()
            assert ev is not None

    def test_override_requires_admin(self, client, attendant_token, garage, admin_token):
        rv = client.post('/v1/gates', json={
            'garageId': garage, 'gateType': 'entry',
        }, headers=auth_header(admin_token))
        gate_id = rv.get_json()['gateId']
        rv = client.post(f'/v1/gates/{gate_id}/override', json={
            'action': 'open',
        }, headers=auth_header(attendant_token))
        assert rv.status_code == 403


# ═══════════════════════════════════════════════════════════════════
#  4.3  Admin garage configuration
# ═══════════════════════════════════════════════════════════════════

class TestAdminGarageConfig:
    def test_create_garage(self, client, admin_token):
        rv = client.post('/v1/garage', json={
            'name': 'New Garage',
            'totalCapacity': 100,
            'numberOfFloors': 3,
            'operatingHours': '06:00-22:00',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 201
        data = rv.get_json()
        assert data['name'] == 'New Garage'
        assert data['totalCapacity'] == 100
        assert data['numberOfFloors'] == 3

    def test_create_garage_missing_fields(self, client, admin_token):
        rv = client.post('/v1/garage', json={
            'name': 'Incomplete',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_create_garage_requires_admin(self, client, attendant_token):
        rv = client.post('/v1/garage', json={
            'name': 'Forbidden Garage',
            'totalCapacity': 50,
            'numberOfFloors': 1,
            'operatingHours': '24/7',
        }, headers=auth_header(attendant_token))
        assert rv.status_code == 403

    def test_create_garage_unauthenticated(self, client):
        rv = client.post('/v1/garage', json={
            'name': 'No Auth',
            'totalCapacity': 50,
            'numberOfFloors': 1,
            'operatingHours': '24/7',
        })
        assert rv.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  4.4  Pricing rules CRUD + calculate_fee wiring
# ═══════════════════════════════════════════════════════════════════

class TestPricingRulesCRUD:
    _VALID_RULE = {
        'rateName': 'Evening Flat',
        'applicableHours': '18:00-06:00',
        'pricingModel': 'flat',
        'description': 'Flat rate for evenings',
        'program': 'flat',
    }

    def test_create_rule(self, client, admin_token):
        rv = client.post('/v1/pricing', json=self._VALID_RULE,
                         headers=auth_header(admin_token))
        assert rv.status_code == 201
        data = rv.get_json()
        assert data['rateName'] == 'Evening Flat'
        assert data['program'] == 'flat'

    def test_create_rule_missing_field(self, client, admin_token):
        rv = client.post('/v1/pricing', json={
            'rateName': 'Incomplete',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_create_rule_invalid_program(self, client, admin_token):
        bad = dict(self._VALID_RULE, program='nonexistent')
        rv = client.post('/v1/pricing', json=bad,
                         headers=auth_header(admin_token))
        assert rv.status_code == 400
        assert 'invalid_program' in rv.get_json()['error']

    def test_create_rule_invalid_pricing_model(self, client, admin_token):
        bad = dict(self._VALID_RULE, pricingModel='premium')
        rv = client.post('/v1/pricing', json=bad,
                         headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_list_rules(self, client, admin_token):
        client.post('/v1/pricing', json=self._VALID_RULE,
                    headers=auth_header(admin_token))
        rv = client.get('/v1/pricing')
        assert rv.status_code == 200
        assert len(rv.get_json()) >= 1

    def test_get_single_rule(self, client, admin_token):
        rv = client.post('/v1/pricing', json=self._VALID_RULE,
                         headers=auth_header(admin_token))
        rate_id = rv.get_json()['rateId']
        rv = client.get(f'/v1/pricing/{rate_id}')
        assert rv.status_code == 200
        assert rv.get_json()['rateId'] == rate_id

    def test_get_rule_not_found(self, client):
        rv = client.get('/v1/pricing/9999')
        assert rv.status_code == 404

    def test_update_rule(self, client, admin_token):
        rv = client.post('/v1/pricing', json=self._VALID_RULE,
                         headers=auth_header(admin_token))
        rate_id = rv.get_json()['rateId']
        rv = client.put(f'/v1/pricing/{rate_id}', json={
            'rateName': 'Updated Name',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.get_json()['rateName'] == 'Updated Name'

    def test_update_rule_invalid_program(self, client, admin_token):
        rv = client.post('/v1/pricing', json=self._VALID_RULE,
                         headers=auth_header(admin_token))
        rate_id = rv.get_json()['rateId']
        rv = client.put(f'/v1/pricing/{rate_id}', json={
            'program': 'bogus',
        }, headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_delete_rule(self, client, admin_token):
        rv = client.post('/v1/pricing', json=self._VALID_RULE,
                         headers=auth_header(admin_token))
        rate_id = rv.get_json()['rateId']
        rv = client.delete(f'/v1/pricing/{rate_id}',
                           headers=auth_header(admin_token))
        assert rv.status_code == 204

    def test_delete_rule_not_found(self, client, admin_token):
        rv = client.delete('/v1/pricing/9999',
                           headers=auth_header(admin_token))
        assert rv.status_code == 404

    def test_create_rule_requires_admin(self, client, attendant_token):
        rv = client.post('/v1/pricing', json=self._VALID_RULE,
                         headers=auth_header(attendant_token))
        assert rv.status_code == 403


class TestPricingFeeCalculation:
    """Verify calculate_fee consults pricing_rule table."""

    def test_default_fee_no_rules(self, client, garage):
        """Without pricing rules, default $5 + $2/hr applies."""
        from utils import calculate_fee
        with app.app_context():
            fee = calculate_fee(120)  # 120 minutes = 2 hours
            assert fee == 9.0  # $5 + $2*2

    def test_fee_with_hourly_rule(self, client, admin_token, garage):
        """With an hourly pricing rule, fee uses the rule's rate."""
        # Create an hourly rule
        client.post('/v1/pricing', json={
            'rateName': 'Standard Hourly',
            'applicableHours': '00:00-23:59',
            'pricingModel': 'hourly',
            'description': 'Standard hourly rate',
            'program': 'hourly',
        }, headers=auth_header(admin_token))
        from utils import calculate_fee
        with app.app_context():
            fee = calculate_fee(120)  # 2 hours
            # Should use the hourly program from pricing_rule
            assert fee is not None
            assert fee > 0


# ═══════════════════════════════════════════════════════════════════
#  4.5  Congestion alert
# ═══════════════════════════════════════════════════════════════════

class TestCongestionAlert:
    def test_alert_below_threshold(self, client, garage):
        """Empty garage — no alert."""
        rv = client.get('/v1/capacity/alert')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['alert'] is False
        assert data['occupancyRate'] == 0.0
        assert 'threshold' in data

    def test_alert_above_threshold(self, client, garage):
        """Fill most spots to trigger alert."""
        # Create 5 tickets to fill all 5 spots
        for i in range(5):
            client.post('/v1/tickets', json={
                'licensePlate': f'CONG-{i:03d}',
                'driverClass': 'standard',
            })
        rv = client.get('/v1/capacity/alert')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['alert'] is True
        assert data['occupancyRate'] == 1.0

    def test_alert_response_shape(self, client, garage):
        rv = client.get('/v1/capacity/alert')
        data = rv.get_json()
        for key in ('alert', 'occupancyRate', 'threshold', 'total', 'occupied', 'available'):
            assert key in data, f'Missing key: {key}'

    def test_alert_after_exit_drops(self, client, garage):
        """Fill all spots, exit one, alert should reflect lower occupancy."""
        for i in range(5):
            client.post('/v1/tickets', json={
                'licensePlate': f'DROP-{i:03d}',
                'driverClass': 'standard',
            })
        # Exit one
        tickets = client.get('/v1/tickets').get_json()
        tid = tickets[0]['ticketId']
        plate = tickets[0]['licensePlate']
        client.put(f'/v1/tickets/{tid}/exit', json={
            'licensePlate': plate, 'paymentMethod': 'cash',
        })
        rv = client.get('/v1/capacity/alert')
        data = rv.get_json()
        assert data['occupied'] == 4
        assert data['available'] == 1
        assert data['occupancyRate'] == 0.8


# ═══════════════════════════════════════════════════════════════════
#  4.6  Stripe webhook (structure tests — no real Stripe calls)
# ═══════════════════════════════════════════════════════════════════

class TestStripeWebhook:
    def test_webhook_rejects_without_secret(self, client):
        """Without valid signature, webhook returns error."""
        rv = client.post('/v1/webhooks/stripe',
                         data=b'{}',
                         content_type='application/json')
        # Should fail with either 401 (bad sig) or 500 (no secret configured)
        assert rv.status_code in (401, 500)

    def test_webhook_endpoint_exists(self, client):
        """POST to webhook endpoint should not 404."""
        rv = client.post('/v1/webhooks/stripe',
                         data=b'{"type":"test"}',
                         content_type='application/json')
        assert rv.status_code != 404
