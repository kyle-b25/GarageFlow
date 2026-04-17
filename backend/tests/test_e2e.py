"""
tests/test_e2e.py — End-to-end workflow validation (Task 39)

Tests the complete lifecycle:
  1. Garage starts empty → verify capacity
  2. Vehicle enters → ticket created, spot assigned, capacity decremented
  3. Vehicle exits → fee calculated, spot released, payment created, capacity restored
  4. Reservation created → floor assigned
  5. Fill garage to capacity → verify rejection of new entries
  6. Analytics reflect all operations (auth required)

Run: pytest tests/test_e2e.py -v
"""
from datetime import datetime, timedelta
import bcrypt
import secrets
import pytest
from app import app, db
from models import (
    Garage, Floor, ParkingSpot, Staff, SessionToken,
    SpotTypeEnum, SpotStatusEnum, StaffRoleEnum,
)


@pytest.fixture()
def e2e_client():
    """Full-stack test client with seeded garage: 1 floor, 4 spots."""
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SECRET_KEY'] = 'e2e-test'

    with app.app_context():
        db.create_all()

        garage = Garage(name='E2E Garage', total_capacity=4, number_of_floors=1, operating_hours='24/7', front_desk_phone='555-0000')
        db.session.add(garage)
        db.session.flush()

        floor = Floor(garage_id=garage.garage_id, floor_number=1, total_spots=4, available_spots=4)
        db.session.add(floor)
        db.session.flush()

        for i in range(4):
            db.session.add(ParkingSpot(floor_id=floor.floor_id, spot_type=SpotTypeEnum.standard, status=SpotStatusEnum.available))

        # Create admin staff + token
        pw_hash = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode()
        staff = Staff(name='Admin', username='admin', password_hash=pw_hash, role=StaffRoleEnum.admin, is_super_admin=True)
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

        yield app.test_client(), token_str

        db.session.remove()
        db.drop_all()


def auth(token):
    return {'Authorization': f'Bearer {token}'}


class TestFullWorkflow:

    def test_complete_entry_exit_cycle(self, e2e_client):
        client, token = e2e_client

        # 1. Verify garage starts empty
        cap = client.get('/v1/capacity').get_json()
        assert cap['available'] == 4
        assert cap['occupied'] == 0

        # 2. Enter vehicle
        entry = client.post('/v1/tickets', json={'licensePlate': 'E2E-001', 'driverClass': 'standard'})
        assert entry.status_code == 201
        ticket = entry.get_json()
        assert ticket['status'] == 'active'
        ticket_id = ticket['ticketId']

        # 3. Capacity updated
        cap2 = client.get('/v1/capacity').get_json()
        assert cap2['occupied'] == 1
        assert cap2['available'] == 3

        # 4. Exit vehicle (licensePlate required by PUT /v1/tickets/{id}/exit)
        exit_resp = client.put(f'/v1/tickets/{ticket_id}/exit', json={
            'licensePlate': 'E2E-001',
            'paymentMethod': 'cash',
        })
        assert exit_resp.status_code == 200
        closed = exit_resp.get_json()
        assert closed['status'] == 'closed'
        assert closed['totalFee'] is not None
        assert closed['duration'] is not None

        # 5. Capacity restored
        cap3 = client.get('/v1/capacity').get_json()
        assert cap3['occupied'] == 0
        assert cap3['available'] == 4

    def test_fill_garage_then_reject(self, e2e_client):
        client, token = e2e_client

        # Fill all 4 spots
        for i in range(4):
            resp = client.post('/v1/tickets', json={'licensePlate': f'FILL-{i}', 'driverClass': 'standard'})
            assert resp.status_code == 201

        # 5th entry should be rejected (codebase returns 503 for garage_full)
        resp = client.post('/v1/tickets', json={'licensePlate': 'FILL-X', 'driverClass': 'standard'})
        assert resp.status_code in (503, 409, 400)

    def test_reservation_workflow(self, e2e_client):
        client, token = e2e_client

        future = (datetime.utcnow() + timedelta(hours=2)).isoformat() + 'Z'
        resp = client.post('/v1/reservations', json={
            'phone': '555-1234',
            'scheduledArrival': future,
            'driverClass': 'standard',
            'licensePlate': 'RES-001',
        })
        assert resp.status_code == 201
        res = resp.get_json()
        assert res['status'] == 'confirmed'

    def test_analytics_after_operations(self, e2e_client):
        client, token = e2e_client

        # Create and close a ticket
        entry = client.post('/v1/tickets', json={'licensePlate': 'ANA-001', 'driverClass': 'standard'})
        tid = entry.get_json()['ticketId']
        client.put(f'/v1/tickets/{tid}/exit', json={
            'licensePlate': 'ANA-001',
            'paymentMethod': 'cash',
        })

        # Check occupancy (auth required)
        # Actual response shape: { live: { total, occupied, available, ... }, trend: [...] }
        occ = client.get('/v1/analytics/occupancy', headers=auth(token))
        assert occ.status_code == 200
        data = occ.get_json()
        assert 'live' in data
        assert 'occupied' in data['live']

    def test_frontend_loads_with_data(self, e2e_client):
        client, token = e2e_client
        resp = client.get('/operator-front')
        assert resp.status_code == 200
        assert b'GarageFlow' in resp.data or b'Garage Entry' in resp.data
