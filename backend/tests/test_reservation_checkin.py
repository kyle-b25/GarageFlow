"""
tests/test_reservation_checkin.py — Reservation check-in integration tests

Covers PUT /v1/reservations/<id>/check:
  - Valid check-in → creates ticket, marks reservation fulfilled
  - Non-existent reservation → 404
  - Already checked-in reservation → 409
  - Cancelled/expired reservation → 409
  - Missing licensePlate → 400
  - Plate mismatch → 409
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, GateEvent, Vehicle, Customer,
    Reservation, Ticket,
    SpotTypeEnum, SpotStatusEnum, GateTypeEnum, GateStatusEnum,
    VehicleTypeEnum, AccountStatusEnum, ReservationStatusEnum,
    TicketStatusEnum,
)


@pytest.fixture()
def reservation_env(client):
    """Seed garage infrastructure and a confirmed reservation."""
    with app.app_context():
        garage = Garage(name='Checkin Garage', total_capacity=4,
                        number_of_floors=1, operating_hours='24/7')
        db.session.add(garage)
        db.session.flush()

        floor = Floor(garage_id=garage.garage_id, floor_number=1,
                      total_spots=4, available_spots=4)
        db.session.add(floor)
        db.session.flush()

        for i in range(4):
            db.session.add(ParkingSpot(
                floor_id=floor.floor_id,
                spot_type=SpotTypeEnum.standard,
                status=SpotStatusEnum.available,
                location_reference=f'S-{i:02d}',
            ))
        db.session.flush()

        customer = Customer(
            name='Test Customer', email='checkin@test.com',
            phone_number='555-CHECK',
            account_status=AccountStatusEnum.active,
        )
        db.session.add(customer)
        db.session.flush()

        vehicle = Vehicle(
            license_plate='CHK-1234', plate_state='NY',
            vehicle_type=VehicleTypeEnum.car,
            customer_id=customer.customer_id,
        )
        db.session.add(vehicle)
        db.session.flush()

        now = datetime.utcnow()
        reservation = Reservation(
            customer_id=customer.customer_id,
            vehicle_id=vehicle.vehicle_id,
            garage_id=garage.garage_id,
            phone='555-CHECK',
            driver_class='standard',
            floor_number=1,
            start_datetime=now - timedelta(minutes=30),
            end_datetime=now + timedelta(hours=2),
            quoted_fee=10.00,
            status=ReservationStatusEnum.confirmed,
        )
        db.session.add(reservation)
        db.session.commit()

        return {
            'reservation_id': reservation.reservation_id,
            'vehicle_id': vehicle.vehicle_id,
            'license_plate': 'CHK-1234',
            'garage_id': garage.garage_id,
            'floor_id': floor.floor_id,
        }


class TestReservationCheckIn:

    def test_valid_checkin(self, client, reservation_env):
        rid = reservation_env['reservation_id']
        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'ticketId' in data
        assert data['licensePlate'] == reservation_env['license_plate']
        assert data['status'] == 'active'
        assert data['spotId'] is not None

        with app.app_context():
            r = Reservation.query.get(rid)
            assert r.status == ReservationStatusEnum.fulfilled

            ticket = Ticket.query.get(data['ticketId'])
            assert ticket is not None
            assert ticket.status == TicketStatusEnum.active
            assert ticket.vehicle_id == reservation_env['vehicle_id']

    def test_nonexistent_reservation(self, client, reservation_env):
        resp = client.put('/v1/reservations/99999/check', json={
            'licensePlate': 'ANYTHING',
        })
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'reservation_not_found'

    def test_already_checked_in(self, client, reservation_env):
        rid = reservation_env['reservation_id']

        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 201

        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'already_checked_in'

    def test_cancelled_reservation(self, client, reservation_env):
        rid = reservation_env['reservation_id']
        with app.app_context():
            r = Reservation.query.get(rid)
            r.status = ReservationStatusEnum.cancelled
            db.session.commit()

        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'reservation_cancelled'

    def test_expired_reservation(self, client, reservation_env):
        rid = reservation_env['reservation_id']
        with app.app_context():
            r = Reservation.query.get(rid)
            r.status = ReservationStatusEnum.expired
            db.session.commit()

        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'reservation_cancelled'

    def test_missing_license_plate(self, client, reservation_env):
        rid = reservation_env['reservation_id']
        resp = client.put(f'/v1/reservations/{rid}/check', json={})
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'missing_required_field'

    def test_plate_mismatch(self, client, reservation_env):
        rid = reservation_env['reservation_id']
        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': 'WRONG-PLATE',
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'plate_mismatch'

    def test_checkin_occupies_spot_and_decrements_floor(self, client, reservation_env):
        """Check-in should occupy a spot and decrement floor available_spots."""
        rid = reservation_env['reservation_id']

        with app.app_context():
            floor = Floor.query.get(reservation_env['floor_id'])
            before_available = floor.available_spots

        resp = client.put(f'/v1/reservations/{rid}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 201
        spot_id = resp.get_json()['spotId']

        with app.app_context():
            spot = ParkingSpot.query.get(spot_id)
            assert spot.status == SpotStatusEnum.occupied
            floor = Floor.query.get(reservation_env['floor_id'])
            assert floor.available_spots == before_available - 1

    def test_r_prefix_format(self, client, reservation_env):
        """Check-in should accept 'R-0001' format reservation IDs."""
        rid = reservation_env['reservation_id']
        r_id = f'R-{rid:04d}'
        resp = client.put(f'/v1/reservations/{r_id}/check', json={
            'licensePlate': reservation_env['license_plate'],
        })
        assert resp.status_code == 201

    def test_invalid_reservation_id_format(self, client, reservation_env):
        resp = client.put('/v1/reservations/not-valid/check', json={
            'licensePlate': 'X',
        })
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_reservation_id'
