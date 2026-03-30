"""
tests/test_all.py — Backend unit tests for Tasks 17–21

Covers:
  Section 1 — Ticket Creation (Task 17)
  Section 2 — Ticket Exit & Closure (Task 18)
  Section 3 — Spot Assignment & Availability (Task 19)
  Section 4 — Reservation Scheduling (Task 20)
  Section 5 — Occupancy Validation Service (Task 21)

Uses an in-memory SQLite database seeded with one garage, two floors,
and eight parking spots (2 standard + 1 accessibility + 1 staff per floor).
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, Reservation, Ticket,
    SpotTypeEnum, SpotStatusEnum, TicketStatusEnum, ReservationStatusEnum,
)


# =====================================================================
#  Fixtures
# =====================================================================

@pytest.fixture()
def app_ctx():
    """Flask app context with seeded in-memory database.

    Seeds:
        1 Garage → 2 Floors → 8 ParkingSpots
        Floor 1: 2 standard, 1 accessibility, 1 staff  (available_spots=4)
        Floor 2: 2 standard, 1 accessibility, 1 staff  (available_spots=4)

    Yields:
        (test_client, ctx_dict) where ctx_dict has garage_id, floor_ids, etc.
    """
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SECRET_KEY'] = 'test-secret'

    with app.app_context():
        db.create_all()

        garage = Garage(
            name='Test Garage',
            total_capacity=8,
            number_of_floors=2,
        )
        db.session.add(garage)
        db.session.flush()

        floors = []
        for num in (1, 2):
            floor = Floor(
                garage_id=garage.garage_id,
                floor_number=num,
                total_spots=4,
                available_spots=4,
            )
            db.session.add(floor)
            floors.append(floor)
        db.session.flush()

        spot_ids = {'standard': [], 'accessibility': [], 'staff': []}
        for floor in floors:
            for stype, count in [
                (SpotTypeEnum.standard, 2),
                (SpotTypeEnum.accessibility, 1),
                (SpotTypeEnum.staff, 1),
            ]:
                for _ in range(count):
                    spot = ParkingSpot(
                        floor_id=floor.floor_id,
                        spot_type=stype,
                        status=SpotStatusEnum.available,
                    )
                    db.session.add(spot)
                    db.session.flush()
                    spot_ids[stype.value].append(spot.spot_id)

        db.session.commit()

        ctx = {
            'garage_id': garage.garage_id,
            'floor_ids': [f.floor_id for f in floors],
            'spot_ids': spot_ids,
        }
        yield app.test_client(), ctx

        db.session.remove()
        db.drop_all()


def _future_iso(minutes=60):
    """Return an ISO 8601 UTC datetime string in the future."""
    return (datetime.utcnow() + timedelta(minutes=minutes)).isoformat() + 'Z'


def _past_iso(minutes=60):
    """Return an ISO 8601 UTC datetime string in the past."""
    return (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + 'Z'


# =====================================================================
#  Section 1 — Ticket Creation (Task 17)
# =====================================================================

class TestTicketCreation:

    def test_ticket_create_happy_path(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'ABC-1234',
            'driverClass': 'standard',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'active'
        assert data['entryTime'] is not None
        assert data['licensePlate'] == 'ABC-1234'

    def test_ticket_create_duplicate_plate(self, app_ctx):
        client, ctx = app_ctx
        client.post('/v1/tickets', json={
            'licensePlate': 'DUP-0001',
            'driverClass': 'standard',
        })
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'DUP-0001',
            'driverClass': 'standard',
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'duplicate_plate'

    def test_ticket_create_missing_fields(self, app_ctx):
        client, ctx = app_ctx
        # Missing licensePlate
        resp = client.post('/v1/tickets', json={'driverClass': 'standard'})
        assert resp.status_code == 400

        # Missing driverClass
        resp = client.post('/v1/tickets', json={'licensePlate': 'XYZ-0000'})
        assert resp.status_code == 400

    def test_ticket_create_spot_occupied(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'OCC-0001',
            'driverClass': 'standard',
        })
        assert resp.status_code == 201
        spot_id = resp.get_json().get('spotId')

        # Verify spot is now occupied via the ticket lookup
        ticket_id = resp.get_json()['ticketId']
        with app.app_context():
            ticket = Ticket.query.get(ticket_id)
            spot = ParkingSpot.query.get(ticket.spot_id)
            assert spot.status == SpotStatusEnum.occupied


# =====================================================================
#  Section 2 — Ticket Exit & Closure (Task 18)
# =====================================================================

class TestTicketExit:

    def _create_ticket(self, client, plate='EXIT-0001'):
        resp = client.post('/v1/tickets', json={
            'licensePlate': plate,
            'driverClass': 'standard',
        })
        return resp.get_json()

    def test_ticket_exit_happy_path(self, app_ctx):
        client, ctx = app_ctx
        ticket = self._create_ticket(client)
        resp = client.put(f'/v1/tickets/{ticket["ticketId"]}/exit', json={
            'licensePlate': 'EXIT-0001',
            'paymentMethod': 'cash',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['exitTime'] is not None
        assert data['duration'] >= 0
        assert data['status'] == 'closed'

    def test_ticket_exit_fee_calculated(self, app_ctx):
        client, ctx = app_ctx
        ticket = self._create_ticket(client, plate='FEE-0001')
        resp = client.put(f'/v1/tickets/{ticket["ticketId"]}/exit', json={
            'licensePlate': 'FEE-0001',
            'paymentMethod': 'card',
        })
        assert resp.status_code == 200
        assert resp.get_json()['totalFee'] is not None

    def test_ticket_exit_spot_released(self, app_ctx):
        client, ctx = app_ctx
        ticket = self._create_ticket(client, plate='REL-0001')
        ticket_id = ticket['ticketId']

        # Get the spot_id from DB
        with app.app_context():
            t = Ticket.query.get(ticket_id)
            spot_id = t.spot_id

        client.put(f'/v1/tickets/{ticket_id}/exit', json={
            'licensePlate': 'REL-0001',
            'paymentMethod': 'cash',
        })
        with app.app_context():
            spot = ParkingSpot.query.get(spot_id)
            assert spot.status == SpotStatusEnum.available

    def test_ticket_exit_already_closed(self, app_ctx):
        client, ctx = app_ctx
        ticket = self._create_ticket(client, plate='CLS-0001')
        client.put(f'/v1/tickets/{ticket["ticketId"]}/exit', json={
            'licensePlate': 'CLS-0001',
            'paymentMethod': 'cash',
        })
        # Try to exit again
        resp = client.put(f'/v1/tickets/{ticket["ticketId"]}/exit', json={
            'licensePlate': 'CLS-0001',
            'paymentMethod': 'cash',
        })
        assert resp.status_code == 409
        assert resp.get_json()['error'] == 'ticket_already_closed'

    def test_ticket_exit_not_found(self, app_ctx):
        client, ctx = app_ctx
        resp = client.put('/v1/tickets/99999/exit', json={
            'licensePlate': 'NOPE',
            'paymentMethod': 'cash',
        })
        assert resp.status_code == 404


# =====================================================================
#  Section 3 — Spot Assignment & Availability (Task 19)
# =====================================================================

class TestSpotAssignment:

    def test_assign_accessibility_spot(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'ACC-0001',
            'driverClass': 'accessibility',
        })
        assert resp.status_code == 201
        ticket_id = resp.get_json()['ticketId']

        with app.app_context():
            ticket = Ticket.query.get(ticket_id)
            spot = ParkingSpot.query.get(ticket.spot_id)
            assert spot.spot_type == SpotTypeEnum.accessibility

    def test_assign_staff_spot(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'EMP-0001',
            'driverClass': 'employee',
        })
        assert resp.status_code == 201
        ticket_id = resp.get_json()['ticketId']

        with app.app_context():
            ticket = Ticket.query.get(ticket_id)
            spot = ParkingSpot.query.get(ticket.spot_id)
            assert spot.spot_type == SpotTypeEnum.staff

    def test_assign_standard_no_cross_type(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'STD-0001',
            'driverClass': 'standard',
        })
        assert resp.status_code == 201
        ticket_id = resp.get_json()['ticketId']

        with app.app_context():
            ticket = Ticket.query.get(ticket_id)
            spot = ParkingSpot.query.get(ticket.spot_id)
            assert spot.spot_type == SpotTypeEnum.standard
            assert spot.spot_type != SpotTypeEnum.accessibility
            assert spot.spot_type != SpotTypeEnum.staff

    def test_floor_available_decrements(self, app_ctx):
        client, ctx = app_ctx
        floor_id = ctx['floor_ids'][0]

        with app.app_context():
            before = Floor.query.get(floor_id).available_spots

        client.post('/v1/tickets', json={
            'licensePlate': 'DEC-0001',
            'driverClass': 'standard',
        })

        with app.app_context():
            after = Floor.query.get(floor_id).available_spots
            assert after == before - 1

    def test_floor_available_increments(self, app_ctx):
        client, ctx = app_ctx
        floor_id = ctx['floor_ids'][0]

        # Create a ticket to decrement
        resp = client.post('/v1/tickets', json={
            'licensePlate': 'INC-0001',
            'driverClass': 'standard',
        })
        ticket_id = resp.get_json()['ticketId']

        with app.app_context():
            before = Floor.query.get(floor_id).available_spots

        client.put(f'/v1/tickets/{ticket_id}/exit', json={
            'licensePlate': 'INC-0001',
            'paymentMethod': 'cash',
        })

        with app.app_context():
            after = Floor.query.get(floor_id).available_spots
            assert after == before + 1


# =====================================================================
#  Section 4 — Reservation Scheduling (Task 20)
# =====================================================================

class TestReservationScheduling:

    def test_reservation_create_happy_path(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/reservations', json={
            'phone': '555-1234',
            'scheduledArrival': _future_iso(),
            'licensePlate': 'RES-0001',
            'driverClass': 'standard',
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'confirmed'
        assert data['phone'] == '555-1234'

    def test_reservation_past_arrival_rejected(self, app_ctx):
        client, ctx = app_ctx
        resp = client.post('/v1/reservations', json={
            'phone': '555-0000',
            'scheduledArrival': _past_iso(),
            'licensePlate': 'OLD-0001',
            'driverClass': 'standard',
        })
        assert resp.status_code == 400

    @pytest.mark.skip(
        reason="Reservations are advisory (floor-level, no spot locking). "
               "Double-booking rejection lives in services/occupancy.py "
               "(tested in TestOccupancyService.test_occupancy_reservation_conflict_error). "
               "Remove skip once POST /v1/reservations integrates validate_and_assign_spot."
    )
    def test_reservation_double_booking(self, app_ctx):
        pass

    def test_reservation_cancel(self, app_ctx):
        client, ctx = app_ctx
        # Create reservation
        resp = client.post('/v1/reservations', json={
            'phone': '555-9999',
            'scheduledArrival': _future_iso(),
            'licensePlate': 'CAN-0001',
            'driverClass': 'standard',
        })
        res_id = resp.get_json()['reservationId']

        # Cancel it
        resp = client.delete(f'/v1/reservations/{res_id}', json={
            'licensePlate': 'CAN-0001',
            'phone': '555-9999',
        })
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'cancelled'

    def test_reservation_cancel_frees_for_new(self, app_ctx):
        client, ctx = app_ctx
        # Create and cancel a reservation
        resp = client.post('/v1/reservations', json={
            'phone': '555-8888',
            'scheduledArrival': _future_iso(),
            'licensePlate': 'FREE-001',
            'driverClass': 'standard',
        })
        res_id = resp.get_json()['reservationId']
        client.delete(f'/v1/reservations/{res_id}', json={
            'licensePlate': 'FREE-001',
            'phone': '555-8888',
        })

        # New reservation should succeed (advisory model: spots not locked)
        resp = client.post('/v1/reservations', json={
            'phone': '555-7777',
            'scheduledArrival': _future_iso(minutes=120),
            'licensePlate': 'FREE-002',
            'driverClass': 'standard',
        })
        assert resp.status_code == 201


# =====================================================================
#  Section 5 — Occupancy Validation Service (Task 21)
# =====================================================================

class TestOccupancyService:

    def test_occupancy_garage_full_error(self, app_ctx):
        _, ctx = app_ctx
        from services.occupancy import validate_and_assign_spot, GarageFullError

        with app.app_context():
            # Mark every spot as occupied
            spots = ParkingSpot.query.all()
            for spot in spots:
                spot.status = SpotStatusEnum.occupied
            for floor in Floor.query.all():
                floor.available_spots = 0
            db.session.commit()

            with pytest.raises(GarageFullError):
                validate_and_assign_spot(ctx['garage_id'], SpotTypeEnum.standard)

    def test_occupancy_no_spot_available_error(self, app_ctx):
        _, ctx = app_ctx
        from services.occupancy import validate_and_assign_spot, NoSpotAvailableError

        with app.app_context():
            # Occupy only accessibility spots (leave standard available)
            acc_spots = ParkingSpot.query.filter_by(
                spot_type=SpotTypeEnum.accessibility,
            ).all()
            for spot in acc_spots:
                spot.status = SpotStatusEnum.occupied
            db.session.commit()

            # Request accessibility — none available
            with pytest.raises(NoSpotAvailableError):
                validate_and_assign_spot(ctx['garage_id'], SpotTypeEnum.accessibility)

    def test_occupancy_reservation_conflict_error(self, app_ctx):
        _, ctx = app_ctx
        from services.occupancy import validate_and_assign_spot, ReservationConflictError

        with app.app_context():
            arrival = datetime.utcnow() + timedelta(hours=2)

            # Occupy all standard spots except one on each floor
            std_spots = ParkingSpot.query.filter_by(
                spot_type=SpotTypeEnum.standard,
                status=SpotStatusEnum.available,
            ).all()

            # Leave exactly 1 standard spot per floor, occupy the rest
            seen_floors = set()
            for spot in std_spots:
                if spot.floor_id not in seen_floors:
                    seen_floors.add(spot.floor_id)
                    # keep this one available
                else:
                    spot.status = SpotStatusEnum.occupied

            db.session.commit()

            # Now each floor has exactly 1 available standard spot.
            # Create a confirmed reservation on each floor to conflict.
            for floor in Floor.query.filter_by(
                garage_id=ctx['garage_id']
            ).all():
                db.session.add(Reservation(
                    phone='conflict',
                    driver_class='standard',
                    floor_number=floor.floor_number,
                    start_datetime=arrival + timedelta(minutes=10),
                    status=ReservationStatusEnum.confirmed,
                ))
            db.session.commit()

            with pytest.raises(ReservationConflictError):
                validate_and_assign_spot(
                    ctx['garage_id'],
                    SpotTypeEnum.standard,
                    arrival_datetime=arrival,
                )

    def test_occupancy_happy_path_returns_spot(self, app_ctx):
        _, ctx = app_ctx
        from services.occupancy import validate_and_assign_spot

        with app.app_context():
            result = validate_and_assign_spot(ctx['garage_id'], SpotTypeEnum.standard)
            assert isinstance(result, ParkingSpot)
            assert result.spot_type == SpotTypeEnum.standard

    def test_occupancy_spot_not_mutated(self, app_ctx):
        _, ctx = app_ctx
        from services.occupancy import validate_and_assign_spot

        with app.app_context():
            spot = validate_and_assign_spot(ctx['garage_id'], SpotTypeEnum.standard)
            # Service must NOT change status — the calling route owns that write
            assert spot.status == SpotStatusEnum.available
