"""
tests/test_pii_redaction.py — PII redaction endpoint tests

Covers DELETE /v1/tickets/<id>/personal:
  - PII fields (phone, license plate) are nulled/redacted after call
  - Returns 404 for non-existent tickets
  - Requires authentication
  - Shared vehicle: creates a redacted clone instead of mutating shared vehicle
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, GateEvent, Vehicle, Ticket,
    SpotTypeEnum, SpotStatusEnum, GateTypeEnum, GateStatusEnum,
    VehicleTypeEnum, TicketStatusEnum,
)
from tests.conftest import create_staff_token, auth_header


@pytest.fixture()
def pii_env(client, auth_token):
    """Seed a ticket with PII (phone + license plate)."""
    with app.app_context():
        garage = Garage(name='PII Garage', total_capacity=5,
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

        vehicle = Vehicle(license_plate='PII-1234', plate_state='TX',
                          vehicle_type=VehicleTypeEnum.car)
        db.session.add(vehicle)
        db.session.flush()

        ticket = Ticket(
            vehicle_id=vehicle.vehicle_id,
            spot_id=spot.spot_id,
            entry_gate_id=gate.gate_id,
            entry_timestamp=datetime.utcnow() - timedelta(hours=1),
            status=TicketStatusEnum.active,
            phone='555-PII-TEST',
        )
        db.session.add(ticket)
        db.session.commit()

        return {
            'ticket_id': ticket.ticket_id,
            'vehicle_id': vehicle.vehicle_id,
            'token': auth_token,
        }


class TestPIIRedaction:

    def test_pii_fields_nulled(self, client, pii_env):
        """Phone is nulled and license plate is redacted."""
        tid = pii_env['ticket_id']
        vid = pii_env['vehicle_id']

        resp = client.delete(f'/v1/tickets/{tid}/personal',
                             headers=auth_header(pii_env['token']))
        assert resp.status_code == 204

        with app.app_context():
            ticket = Ticket.query.get(tid)
            assert ticket.phone is None

            vehicle = Vehicle.query.get(ticket.vehicle_id)
            assert vehicle.license_plate.startswith('REDACTED-')

    def test_original_plate_gone(self, client, pii_env):
        """Original license plate should no longer exist in DB."""
        tid = pii_env['ticket_id']

        client.delete(f'/v1/tickets/{tid}/personal',
                      headers=auth_header(pii_env['token']))

        with app.app_context():
            match = Vehicle.query.filter_by(license_plate='PII-1234').first()
            assert match is None

    def test_not_found(self, client, pii_env):
        resp = client.delete('/v1/tickets/99999/personal',
                             headers=auth_header(pii_env['token']))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'ticket_not_found'

    def test_unauthenticated(self, client, pii_env):
        resp = client.delete(f'/v1/tickets/{pii_env["ticket_id"]}/personal')
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'unauthorized'

    def test_shared_vehicle_creates_redacted_clone(self, client, pii_env):
        """When multiple tickets share a vehicle, redaction clones instead of mutating."""
        with app.app_context():
            spot = ParkingSpot.query.filter_by(
                status=SpotStatusEnum.available
            ).first()
            gate = GateEvent.query.first()

            ticket2 = Ticket(
                vehicle_id=pii_env['vehicle_id'],
                spot_id=spot.spot_id,
                entry_gate_id=gate.gate_id,
                entry_timestamp=datetime.utcnow(),
                status=TicketStatusEnum.active,
                phone='555-SHARED',
            )
            db.session.add(ticket2)
            db.session.commit()
            ticket2_id = ticket2.ticket_id

        resp = client.delete(f'/v1/tickets/{pii_env["ticket_id"]}/personal',
                             headers=auth_header(pii_env['token']))
        assert resp.status_code == 204

        with app.app_context():
            t1 = Ticket.query.get(pii_env['ticket_id'])
            assert t1.vehicle_id != pii_env['vehicle_id']
            redacted_v = Vehicle.query.get(t1.vehicle_id)
            assert redacted_v.license_plate.startswith('REDACTED-')

            t2 = Ticket.query.get(ticket2_id)
            assert t2.vehicle_id == pii_env['vehicle_id']
            original_v = Vehicle.query.get(pii_env['vehicle_id'])
            assert original_v.license_plate == 'PII-1234'

    def test_idempotent(self, client, pii_env):
        """Calling redaction twice should not error."""
        tid = pii_env['ticket_id']
        headers = auth_header(pii_env['token'])

        resp1 = client.delete(f'/v1/tickets/{tid}/personal', headers=headers)
        assert resp1.status_code == 204

        resp2 = client.delete(f'/v1/tickets/{tid}/personal', headers=headers)
        assert resp2.status_code == 204
