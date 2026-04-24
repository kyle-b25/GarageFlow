"""
tests/test_spaces_floors.py — Space and Floor CRUD endpoint tests

Covers:
  GET    /v1/floors                          — List floors
  POST   /v1/floors                          — Create floor
  GET    /v1/floors/<id>                     — Single floor
  PUT    /v1/floors/<id>                     — Update floor
  DELETE /v1/floors/<id>                     — Delete floor
  GET    /v1/floors/<id>/spaces              — List spaces on floor
  POST   /v1/floors/<id>/spaces              — Create space
  GET    /v1/floors/<id>/spaces/<id>         — Single space
  PUT    /v1/floors/<id>/spaces/<id>         — Update space
  DELETE /v1/floors/<id>/spaces/<id>         — Delete space
  GET    /v1/spaces                          — List all spaces
  GET    /v1/spaces/available                — Available spaces

Verifies status codes, DB state, auth enforcement.
"""

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot,
    SpotTypeEnum, SpotStatusEnum,
)
from tests.conftest import create_staff_token, auth_header


@pytest.fixture()
def seeded(client, auth_token):
    """Seed a garage with one floor and two spots."""
    with app.app_context():
        garage = Garage(name='Test Garage', total_capacity=2,
                        number_of_floors=1, operating_hours='24/7')
        db.session.add(garage)
        db.session.flush()

        floor = Floor(garage_id=garage.garage_id, floor_number=1,
                      floor_name='Ground', total_spots=2, available_spots=2)
        db.session.add(floor)
        db.session.flush()

        spot1 = ParkingSpot(floor_id=floor.floor_id, spot_type=SpotTypeEnum.standard,
                            status=SpotStatusEnum.available, location_reference='A-01')
        spot2 = ParkingSpot(floor_id=floor.floor_id, spot_type=SpotTypeEnum.accessibility,
                            status=SpotStatusEnum.available, location_reference='A-02')
        db.session.add_all([spot1, spot2])
        db.session.commit()

        return {
            'garage_id': garage.garage_id,
            'floor_id': floor.floor_id,
            'spot_ids': [spot1.spot_id, spot2.spot_id],
            'token': auth_token,
        }


# ======================================================================
#  Floor CRUD
# ======================================================================

class TestListFloors:

    def test_happy_path(self, client, seeded):
        resp = client.get('/v1/floors')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert 'floorId' in data[0]

    def test_filter_by_garage(self, client, seeded):
        resp = client.get(f'/v1/floors?garage_id={seeded["garage_id"]}')
        assert resp.status_code == 200
        assert len(resp.get_json()) >= 1

    def test_empty_garage_filter(self, client, seeded):
        resp = client.get('/v1/floors?garage_id=99999')
        assert resp.status_code == 200
        assert resp.get_json() == []


class TestCreateFloor:

    def test_happy_path(self, client, seeded):
        resp = client.post('/v1/floors', json={
            'garageId': seeded['garage_id'],
            'floorNumber': 2,
            'totalSpots': 10,
            'floorName': 'Level 2',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['floorNumber'] == 2
        assert data['floorName'] == 'Level 2'
        assert data['totalSpots'] == 10

    def test_missing_fields(self, client, seeded):
        resp = client.post('/v1/floors', json={'garageId': seeded['garage_id']},
                           headers=auth_header(seeded['token']))
        assert resp.status_code == 400

    def test_garage_not_found(self, client, seeded):
        resp = client.post('/v1/floors', json={
            'garageId': 99999, 'floorNumber': 1, 'totalSpots': 5,
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 404

    def test_unauthenticated(self, client, seeded):
        resp = client.post('/v1/floors', json={
            'garageId': seeded['garage_id'], 'floorNumber': 3, 'totalSpots': 5,
        })
        assert resp.status_code == 401

    def test_attendant_rejected(self, client, seeded, attendant_token):
        resp = client.post('/v1/floors', json={
            'garageId': seeded['garage_id'], 'floorNumber': 3, 'totalSpots': 5,
        }, headers=auth_header(attendant_token))
        assert resp.status_code == 403


class TestGetFloor:

    def test_happy_path(self, client, seeded):
        resp = client.get(f'/v1/floors/{seeded["floor_id"]}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['floorId'] == seeded['floor_id']
        assert data['floorNumber'] == 1

    def test_not_found(self, client, seeded):
        resp = client.get('/v1/floors/99999')
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'floor_not_found'


class TestUpdateFloor:

    def test_update_name(self, client, seeded):
        resp = client.put(f'/v1/floors/{seeded["floor_id"]}', json={
            'floorName': 'Renamed Floor',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 200
        assert resp.get_json()['floorName'] == 'Renamed Floor'

    def test_update_total_spots(self, client, seeded):
        resp = client.put(f'/v1/floors/{seeded["floor_id"]}', json={
            'totalSpots': 20,
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 200
        assert resp.get_json()['totalSpots'] == 20

    def test_not_found(self, client, seeded):
        resp = client.put('/v1/floors/99999', json={'floorName': 'X'},
                          headers=auth_header(seeded['token']))
        assert resp.status_code == 404

    def test_unauthenticated(self, client, seeded):
        resp = client.put(f'/v1/floors/{seeded["floor_id"]}',
                          json={'floorName': 'X'})
        assert resp.status_code == 401


class TestDeleteFloor:

    def test_happy_path(self, client, seeded):
        # Create an empty floor to delete
        resp = client.post('/v1/floors', json={
            'garageId': seeded['garage_id'], 'floorNumber': 9, 'totalSpots': 1,
        }, headers=auth_header(seeded['token']))
        new_floor_id = resp.get_json()['floorId']

        resp = client.delete(f'/v1/floors/{new_floor_id}',
                             headers=auth_header(seeded['token']))
        assert resp.status_code == 200

        # Verify gone
        resp = client.get(f'/v1/floors/{new_floor_id}')
        assert resp.status_code == 404

    def test_not_found(self, client, seeded):
        resp = client.delete('/v1/floors/99999',
                             headers=auth_header(seeded['token']))
        assert resp.status_code == 404

    def test_floor_with_occupied_spots_blocked(self, client, seeded):
        """Cannot delete floor when spots are occupied."""
        with app.app_context():
            spot = ParkingSpot.query.get(seeded['spot_ids'][0])
            spot.status = SpotStatusEnum.occupied
            db.session.commit()

        resp = client.delete(f'/v1/floors/{seeded["floor_id"]}',
                             headers=auth_header(seeded['token']))
        assert resp.status_code == 400

    def test_unauthenticated(self, client, seeded):
        resp = client.delete(f'/v1/floors/{seeded["floor_id"]}')
        assert resp.status_code == 401


# ======================================================================
#  Space CRUD (nested under floors)
# ======================================================================

class TestListFloorSpaces:

    def test_happy_path(self, client, seeded):
        resp = client.get(f'/v1/floors/{seeded["floor_id"]}/spaces')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert 'spaceId' in data[0]
        assert 'type' in data[0]

    def test_filter_by_status(self, client, seeded):
        resp = client.get(f'/v1/floors/{seeded["floor_id"]}/spaces?status=available')
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2

    def test_invalid_status(self, client, seeded):
        resp = client.get(f'/v1/floors/{seeded["floor_id"]}/spaces?status=bogus')
        assert resp.status_code == 400

    def test_floor_not_found(self, client, seeded):
        resp = client.get('/v1/floors/99999/spaces')
        assert resp.status_code == 404


class TestCreateSpace:

    def test_happy_path(self, client, seeded):
        resp = client.post(f'/v1/floors/{seeded["floor_id"]}/spaces', json={
            'type': 'standard',
            'locationReference': 'B-01',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['type'] == 'standard'
        assert data['locationReference'] == 'B-01'
        assert data['status'] == 'available'

    def test_creates_updates_floor_counts(self, client, seeded):
        """Creating a space increments floor total_spots and available_spots."""
        before = client.get(f'/v1/floors/{seeded["floor_id"]}').get_json()

        client.post(f'/v1/floors/{seeded["floor_id"]}/spaces', json={
            'type': 'standard',
        }, headers=auth_header(seeded['token']))

        after = client.get(f'/v1/floors/{seeded["floor_id"]}').get_json()
        assert after['totalSpots'] == before['totalSpots'] + 1
        assert after['availableSpots'] == before['availableSpots'] + 1

    def test_missing_type(self, client, seeded):
        resp = client.post(f'/v1/floors/{seeded["floor_id"]}/spaces', json={},
                           headers=auth_header(seeded['token']))
        assert resp.status_code == 400

    def test_invalid_type(self, client, seeded):
        resp = client.post(f'/v1/floors/{seeded["floor_id"]}/spaces', json={
            'type': 'nonexistent',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 400

    def test_floor_not_found(self, client, seeded):
        resp = client.post('/v1/floors/99999/spaces', json={'type': 'standard'},
                           headers=auth_header(seeded['token']))
        assert resp.status_code == 404

    def test_unauthenticated(self, client, seeded):
        resp = client.post(f'/v1/floors/{seeded["floor_id"]}/spaces',
                           json={'type': 'standard'})
        assert resp.status_code == 401


class TestGetSpace:

    def test_happy_path(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.get(f'/v1/floors/{fid}/spaces/{sid}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['spaceId'] == sid

    def test_space_not_found(self, client, seeded):
        resp = client.get(f'/v1/floors/{seeded["floor_id"]}/spaces/99999')
        assert resp.status_code == 404

    def test_floor_not_found(self, client, seeded):
        resp = client.get(f'/v1/floors/99999/spaces/{seeded["spot_ids"][0]}')
        assert resp.status_code == 404


class TestUpdateSpace:

    def test_update_type(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.put(f'/v1/floors/{fid}/spaces/{sid}', json={
            'type': 'eco',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 200
        assert resp.get_json()['type'] == 'eco'

    def test_update_location_reference(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.put(f'/v1/floors/{fid}/spaces/{sid}', json={
            'locationReference': 'Z-99',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 200
        assert resp.get_json()['locationReference'] == 'Z-99'

    def test_update_status_to_out_of_order(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.put(f'/v1/floors/{fid}/spaces/{sid}', json={
            'status': 'out_of_order',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'out_of_order'

        # Floor available_spots should decrement
        floor = client.get(f'/v1/floors/{fid}').get_json()
        assert floor['availableSpots'] == 1

    def test_invalid_type(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.put(f'/v1/floors/{fid}/spaces/{sid}', json={
            'type': 'imaginary',
        }, headers=auth_header(seeded['token']))
        assert resp.status_code == 400

    def test_space_not_found(self, client, seeded):
        resp = client.put(f'/v1/floors/{seeded["floor_id"]}/spaces/99999',
                          json={'type': 'standard'},
                          headers=auth_header(seeded['token']))
        assert resp.status_code == 404

    def test_unauthenticated(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.put(f'/v1/floors/{fid}/spaces/{sid}',
                          json={'type': 'eco'})
        assert resp.status_code == 401


class TestDeleteSpace:

    def test_happy_path(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]

        resp = client.delete(f'/v1/floors/{fid}/spaces/{sid}',
                             headers=auth_header(seeded['token']))
        assert resp.status_code == 200

        # Verify gone
        resp = client.get(f'/v1/floors/{fid}/spaces/{sid}')
        assert resp.status_code == 404

    def test_delete_updates_floor_counts(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        before = client.get(f'/v1/floors/{fid}').get_json()

        client.delete(f'/v1/floors/{fid}/spaces/{sid}',
                      headers=auth_header(seeded['token']))

        after = client.get(f'/v1/floors/{fid}').get_json()
        assert after['totalSpots'] == before['totalSpots'] - 1
        assert after['availableSpots'] == before['availableSpots'] - 1

    def test_occupied_spot_blocked(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        with app.app_context():
            spot = ParkingSpot.query.get(sid)
            spot.status = SpotStatusEnum.occupied
            db.session.commit()

        resp = client.delete(f'/v1/floors/{fid}/spaces/{sid}',
                             headers=auth_header(seeded['token']))
        assert resp.status_code == 400

    def test_space_not_found(self, client, seeded):
        resp = client.delete(f'/v1/floors/{seeded["floor_id"]}/spaces/99999',
                             headers=auth_header(seeded['token']))
        assert resp.status_code == 404

    def test_unauthenticated(self, client, seeded):
        fid = seeded['floor_id']
        sid = seeded['spot_ids'][0]
        resp = client.delete(f'/v1/floors/{fid}/spaces/{sid}')
        assert resp.status_code == 401


# ======================================================================
#  Global space listing
# ======================================================================

class TestListAllSpaces:

    def test_happy_path(self, client, seeded):
        resp = client.get('/v1/spaces')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_filter_by_garage(self, client, seeded):
        resp = client.get(f'/v1/spaces?garage_id={seeded["garage_id"]}')
        assert resp.status_code == 200
        assert len(resp.get_json()) >= 2


class TestListAvailableSpaces:

    def test_happy_path(self, client, seeded):
        resp = client.get('/v1/spaces/available')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 2
        for spot in data:
            assert spot['status'] == 'available'

    def test_filter_by_type(self, client, seeded):
        resp = client.get('/v1/spaces/available?type=accessibility')
        assert resp.status_code == 200
        data = resp.get_json()
        for spot in data:
            assert spot['type'] == 'accessibility'

    def test_invalid_type(self, client, seeded):
        resp = client.get('/v1/spaces/available?type=bogus')
        assert resp.status_code == 400

    def test_none_available(self, client, seeded):
        with app.app_context():
            for spot in ParkingSpot.query.filter_by(floor_id=seeded['floor_id']).all():
                spot.status = SpotStatusEnum.occupied
            db.session.commit()

        resp = client.get('/v1/spaces/available')
        assert resp.status_code == 200
        assert resp.get_json() == []
