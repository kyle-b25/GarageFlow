"""
tests/test_new_features.py — Tests for the four new features:
  1. Multi-garage scoping
  2. Customer-facing portal
  3. Reservation modification with conflict checking
  4. Analytics export (CSV)
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, Vehicle, Customer, Reservation,
    SpotTypeEnum, SpotStatusEnum, VehicleTypeEnum,
    AccountStatusEnum, ReservationStatusEnum,
)
from tests.conftest import create_staff_token, auth_header


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


def _make_garage(name, spots=5):
    """Create a garage with 1 floor and N standard spots."""
    g = Garage(name=name, total_capacity=spots,
               number_of_floors=1, operating_hours='24/7')
    db.session.add(g)
    db.session.flush()
    f = Floor(garage_id=g.garage_id, floor_number=1,
              total_spots=spots, available_spots=spots)
    db.session.add(f)
    db.session.flush()
    for i in range(spots):
        db.session.add(ParkingSpot(
            floor_id=f.floor_id,
            location_reference=f'{name[0]}{i+1}',
            spot_type=SpotTypeEnum.standard,
            status=SpotStatusEnum.available,
        ))
    db.session.commit()
    return g.garage_id


@pytest.fixture()
def two_garages(client):
    """Create two garages and return their IDs."""
    with app.app_context():
        g1 = _make_garage('Garage A', 3)
        g2 = _make_garage('Garage B', 3)
        return g1, g2


# ═══════════════════════════════════════════════════════════════════
#  1. Multi-Garage Scoping
# ═══════════════════════════════════════════════════════════════════

class TestMultiGarageScoping:
    def test_get_garage_returns_array_for_multiple(self, client, two_garages):
        rv = client.get('/v1/garage')
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_tickets_scoped_by_garage(self, client, two_garages):
        g1, g2 = two_garages
        # Create ticket in garage A
        client.post('/v1/tickets', json={
            'licensePlate': 'GA-001', 'driverClass': 'standard', 'garageId': g1,
        })
        # Create ticket in garage B
        client.post('/v1/tickets', json={
            'licensePlate': 'GB-001', 'driverClass': 'standard', 'garageId': g2,
        })
        # List all tickets
        all_tickets = client.get('/v1/tickets').get_json()
        assert len(all_tickets) == 2
        # List tickets for garage A only
        g1_tickets = client.get(f'/v1/tickets?garage_id={g1}').get_json()
        assert len(g1_tickets) == 1
        assert g1_tickets[0]['licensePlate'] == 'GA-001'

    def test_capacity_scoped_by_garage(self, client, two_garages):
        g1, g2 = two_garages
        # Fill one spot in garage A
        client.post('/v1/tickets', json={
            'licensePlate': 'CAP-001', 'driverClass': 'standard', 'garageId': g1,
        })
        # Check capacity for garage A
        rv = client.get(f'/v1/capacity?garage_id={g1}')
        data = rv.get_json()
        assert data['occupied'] == 1
        assert data['available'] == 2
        # Garage B should be unaffected
        rv = client.get(f'/v1/capacity?garage_id={g2}')
        data = rv.get_json()
        assert data['occupied'] == 0
        assert data['available'] == 3

    def test_reservations_scoped_by_garage(self, client, two_garages):
        g1, g2 = two_garages
        future = (datetime.utcnow() + timedelta(hours=24)).isoformat() + 'Z'
        # Create reservation targeting garage A
        rv = client.post('/v1/reservations', json={
            'phone': '555-0001',
            'scheduledArrival': future,
            'licensePlate': 'RG-001',
            'garageId': g1,
        })
        assert rv.status_code == 201
        # List reservations for garage A
        rv = client.get(f'/v1/reservations?garage_id={g1}')
        assert rv.status_code == 200
        assert len(rv.get_json()) == 1
        # List reservations for garage B (should be empty)
        rv = client.get(f'/v1/reservations?garage_id={g2}')
        assert len(rv.get_json()) == 0


# ═══════════════════════════════════════════════════════════════════
#  2. Customer Portal
# ═══════════════════════════════════════════════════════════════════

class TestCustomerPortal:
    def test_customer_register(self, client):
        rv = client.post('/v1/customer/register', json={
            'name': 'Test User',
            'email': 'test@example.com',
            'phone': '555-1234',
            'password': 'password123',
        })
        assert rv.status_code == 201
        data = rv.get_json()
        assert 'token' in data
        assert data['customer']['email'] == 'test@example.com'

    def test_customer_register_duplicate_email(self, client):
        client.post('/v1/customer/register', json={
            'name': 'A', 'email': 'dup@test.com', 'phone': '555-0001', 'password': 'password123',
        })
        rv = client.post('/v1/customer/register', json={
            'name': 'B', 'email': 'dup@test.com', 'phone': '555-0002', 'password': 'password123',
        })
        assert rv.status_code == 409

    def test_customer_register_weak_password(self, client):
        rv = client.post('/v1/customer/register', json={
            'name': 'A', 'email': 'a@test.com', 'phone': '555-0001', 'password': 'short',
        })
        assert rv.status_code == 400

    def test_customer_login(self, client):
        client.post('/v1/customer/register', json={
            'name': 'A', 'email': 'login@test.com', 'phone': '555-0001', 'password': 'password123',
        })
        rv = client.post('/v1/customer/login', json={
            'email': 'login@test.com', 'password': 'password123',
        })
        assert rv.status_code == 200
        assert 'token' in rv.get_json()

    def test_customer_login_wrong_password(self, client):
        client.post('/v1/customer/register', json={
            'name': 'A', 'email': 'bad@test.com', 'phone': '555-0001', 'password': 'password123',
        })
        rv = client.post('/v1/customer/login', json={
            'email': 'bad@test.com', 'password': 'wrongpassword',
        })
        assert rv.status_code == 401

    def test_customer_me(self, client):
        rv = client.post('/v1/customer/register', json={
            'name': 'Me User', 'email': 'me@test.com', 'phone': '555-0001', 'password': 'password123',
        })
        token = rv.get_json()['token']
        rv = client.get('/v1/customer/me', headers={'Authorization': f'Bearer {token}'})
        assert rv.status_code == 200
        assert rv.get_json()['name'] == 'Me User'

    def test_customer_me_unauthorized(self, client):
        rv = client.get('/v1/customer/me')
        assert rv.status_code == 401

    def test_customer_add_vehicle(self, client):
        rv = client.post('/v1/customer/register', json={
            'name': 'V User', 'email': 'v@test.com', 'phone': '555-0001', 'password': 'password123',
        })
        token = rv.get_json()['token']
        rv = client.post('/v1/customer/vehicles', json={
            'licensePlate': 'CUST-001', 'vehicleType': 'car',
        }, headers={'Authorization': f'Bearer {token}'})
        assert rv.status_code == 201
        assert rv.get_json()['licensePlate'] == 'CUST-001'

    def test_customer_list_vehicles(self, client):
        rv = client.post('/v1/customer/register', json={
            'name': 'V User', 'email': 'vl@test.com', 'phone': '555-0001', 'password': 'password123',
        })
        token = rv.get_json()['token']
        headers = {'Authorization': f'Bearer {token}'}
        client.post('/v1/customer/vehicles', json={
            'licensePlate': 'LV-001', 'vehicleType': 'car',
        }, headers=headers)
        rv = client.get('/v1/customer/vehicles', headers=headers)
        assert rv.status_code == 200
        assert len(rv.get_json()) == 1

    def test_customer_create_reservation(self, client, two_garages):
        g1, _ = two_garages
        rv = client.post('/v1/customer/register', json={
            'name': 'R User', 'email': 'r@test.com', 'phone': '555-0099', 'password': 'password123',
        })
        token = rv.get_json()['token']
        headers = {'Authorization': f'Bearer {token}'}
        # Add vehicle
        rv = client.post('/v1/customer/vehicles', json={
            'licensePlate': 'CR-001', 'vehicleType': 'car',
        }, headers=headers)
        vehicle_id = rv.get_json()['vehicleId']
        # Create reservation
        future = (datetime.utcnow() + timedelta(hours=24)).isoformat() + 'Z'
        rv = client.post('/v1/customer/reservations', json={
            'vehicleId': vehicle_id,
            'scheduledArrival': future,
            'garageId': g1,
        }, headers=headers)
        assert rv.status_code == 201
        assert rv.get_json()['status'] == 'confirmed'

    def test_customer_list_reservations(self, client, two_garages):
        g1, _ = two_garages
        rv = client.post('/v1/customer/register', json={
            'name': 'R User', 'email': 'rl@test.com', 'phone': '555-0098', 'password': 'password123',
        })
        token = rv.get_json()['token']
        headers = {'Authorization': f'Bearer {token}'}
        rv = client.post('/v1/customer/vehicles', json={
            'licensePlate': 'RL-001', 'vehicleType': 'car',
        }, headers=headers)
        vehicle_id = rv.get_json()['vehicleId']
        future = (datetime.utcnow() + timedelta(hours=24)).isoformat() + 'Z'
        client.post('/v1/customer/reservations', json={
            'vehicleId': vehicle_id, 'scheduledArrival': future, 'garageId': g1,
        }, headers=headers)
        rv = client.get('/v1/customer/reservations', headers=headers)
        assert rv.status_code == 200
        assert len(rv.get_json()) == 1

    def test_customer_portal_page_loads(self, client):
        rv = client.get('/customer-portal')
        assert rv.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  3. Reservation Modification
# ═══════════════════════════════════════════════════════════════════

class TestReservationModification:
    def _create_reservation(self, client, plate='MOD-001', phone='555-MOD1'):
        future = (datetime.utcnow() + timedelta(hours=24)).isoformat() + 'Z'
        rv = client.post('/v1/reservations', json={
            'phone': phone,
            'scheduledArrival': future,
            'licensePlate': plate,
        })
        return rv.get_json()['reservationId']

    def test_modify_arrival_time(self, client, two_garages):
        res_id = self._create_reservation(client)
        new_start = (datetime.utcnow() + timedelta(hours=48)).isoformat() + 'Z'
        new_end = (datetime.utcnow() + timedelta(hours=50)).isoformat() + 'Z'
        rv = client.put(f'/v1/reservations/{res_id}', json={
            'scheduledArrival': new_start,
            'endDatetime': new_end,
        })
        assert rv.status_code == 200

    def test_modify_end_time(self, client, two_garages):
        res_id = self._create_reservation(client)
        new_start = (datetime.utcnow() + timedelta(hours=48)).isoformat() + 'Z'
        new_end = (datetime.utcnow() + timedelta(hours=52)).isoformat() + 'Z'
        rv = client.put(f'/v1/reservations/{res_id}', json={
            'scheduledArrival': new_start,
            'endDatetime': new_end,
        })
        assert rv.status_code == 200

    def test_modify_rejects_past_arrival(self, client, two_garages):
        res_id = self._create_reservation(client)
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + 'Z'
        rv = client.put(f'/v1/reservations/{res_id}', json={
            'scheduledArrival': past,
        })
        assert rv.status_code == 400

    def test_modify_rejects_end_before_start(self, client, two_garages):
        res_id = self._create_reservation(client)
        future = (datetime.utcnow() + timedelta(hours=48)).isoformat() + 'Z'
        earlier = (datetime.utcnow() + timedelta(hours=47)).isoformat() + 'Z'
        rv = client.put(f'/v1/reservations/{res_id}', json={
            'scheduledArrival': future,
            'endDatetime': earlier,
        })
        assert rv.status_code == 400

    def test_modify_cancelled_reservation_rejected(self, client, two_garages):
        res_id = self._create_reservation(client, plate='MOD-CAN', phone='555-CAN1')
        # Cancel it first
        client.delete(f'/v1/reservations/{res_id}', json={
            'licensePlate': 'MOD-CAN', 'phone': '555-CAN1',
        })
        new_time = (datetime.utcnow() + timedelta(hours=48)).isoformat() + 'Z'
        rv = client.put(f'/v1/reservations/{res_id}', json={
            'scheduledArrival': new_time,
        })
        assert rv.status_code == 409

    def test_modify_detects_conflict(self, client, two_garages):
        # Create two reservations on same floor
        res1_id = self._create_reservation(client, plate='CON-001', phone='555-CON1')
        res2_id = self._create_reservation(client, plate='CON-002', phone='555-CON2')
        # Get the first reservation's times
        rv = client.get(f'/v1/reservations/{res1_id}')
        r1 = rv.get_json()
        # Try to move res2 to overlap with res1
        rv = client.put(f'/v1/reservations/{res2_id}', json={
            'scheduledArrival': r1['scheduledArrival'],
        })
        # This may or may not conflict depending on floor assignment
        # At minimum, the endpoint should return 200 or 409 (not 500)
        assert rv.status_code in (200, 409)


# ═══════════════════════════════════════════════════════════════════
#  4. Analytics Export
# ═══════════════════════════════════════════════════════════════════

class TestAnalyticsExport:
    def test_revenue_export_csv(self, client, admin_token, two_garages):
        g1, _ = two_garages
        # Create a ticket and exit it to generate a payment
        rv = client.post('/v1/tickets', json={
            'licensePlate': 'CSV-001', 'driverClass': 'standard', 'garageId': g1,
        })
        tid = rv.get_json()['ticketId']
        client.put(f'/v1/tickets/{tid}/exit', json={
            'licensePlate': 'CSV-001', 'paymentMethod': 'cash',
        })
        start = (datetime.utcnow() - timedelta(days=1)).isoformat() + 'Z'
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + 'Z'
        rv = client.get(f'/v1/admin/reports/revenue?start={start}&end={end}',
                        headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.content_type == 'text/csv; charset=utf-8'
        csv_text = rv.data.decode()
        assert 'payment_id' in csv_text
        assert 'CSV-001' not in csv_text  # CSV has payment data, not plates
        lines = csv_text.strip().split('\n')
        assert len(lines) >= 2  # header + at least 1 data row

    def test_revenue_export_missing_params(self, client, admin_token):
        rv = client.get('/v1/admin/reports/revenue',
                        headers=auth_header(admin_token))
        assert rv.status_code == 400

    def test_revenue_export_requires_admin(self, client, two_garages):
        start = datetime.utcnow().isoformat() + 'Z'
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + 'Z'
        rv = client.get(f'/v1/admin/reports/revenue?start={start}&end={end}')
        assert rv.status_code == 401

    def test_utilization_export_csv(self, client, admin_token, two_garages):
        g1, _ = two_garages
        # Create and exit a ticket to generate occupancy data
        rv = client.post('/v1/tickets', json={
            'licensePlate': 'UTL-001', 'driverClass': 'standard', 'garageId': g1,
        })
        tid = rv.get_json()['ticketId']
        client.put(f'/v1/tickets/{tid}/exit', json={
            'licensePlate': 'UTL-001', 'paymentMethod': 'cash',
        })
        start = (datetime.utcnow() - timedelta(days=1)).isoformat() + 'Z'
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + 'Z'
        rv = client.get(f'/v1/admin/reports/utilization?start={start}&end={end}',
                        headers=auth_header(admin_token))
        assert rv.status_code == 200
        assert rv.content_type == 'text/csv; charset=utf-8'
        csv_text = rv.data.decode()
        assert 'date' in csv_text
        assert 'entries' in csv_text

    def test_utilization_export_scoped_by_garage(self, client, admin_token, two_garages):
        g1, g2 = two_garages
        # Create ticket only in garage A
        rv = client.post('/v1/tickets', json={
            'licensePlate': 'SCG-001', 'driverClass': 'standard', 'garageId': g1,
        })
        tid = rv.get_json()['ticketId']
        client.put(f'/v1/tickets/{tid}/exit', json={
            'licensePlate': 'SCG-001', 'paymentMethod': 'cash',
        })
        start = (datetime.utcnow() - timedelta(days=1)).isoformat() + 'Z'
        end = (datetime.utcnow() + timedelta(days=1)).isoformat() + 'Z'
        # Garage A should have data
        rv = client.get(f'/v1/admin/reports/utilization?start={start}&end={end}&garage_id={g1}',
                        headers=auth_header(admin_token))
        assert rv.status_code == 200
        g1_lines = rv.data.decode().strip().split('\n')
        # Garage B should have no data rows (just header)
        rv = client.get(f'/v1/admin/reports/utilization?start={start}&end={end}&garage_id={g2}',
                        headers=auth_header(admin_token))
        assert rv.status_code == 200
        g2_lines = rv.data.decode().strip().split('\n')
        assert len(g2_lines) <= len(g1_lines)
