"""
tests/test_analytics.py — Analytics module tests

Covers utilization calculation, occupancy live/trend, peak-hours ranking,
empty-log edge cases, and invalid date range validation.

Run:  pytest tests/test_analytics.py -v
"""

from datetime import datetime, timedelta

import pytest

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, OccupancyLog,
    SpotTypeEnum, SpotStatusEnum, OccupancyChangeEnum,
)


# ======================================================================
#  Fixtures
# ======================================================================

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


@pytest.fixture()
def seeded(client):
    """
    Seed a garage with 1 floor, 5 spots, and occupancy events.

    Timeline (all on 2026-03-20):
      08:00  spot 1 occupied
      09:00  spot 2 occupied
      10:00  spot 3 occupied     -> 3 occupied
      14:00  spot 1 freed        -> 2 occupied
      16:00  spot 2 freed        -> 1 occupied
      (spot 3 stays occupied — mirrors live status)

    Returns dict with IDs for assertions.
    """
    with app.app_context():
        garage = Garage(name='Test', total_capacity=5, number_of_floors=1)
        db.session.add(garage)
        db.session.flush()

        floor = Floor(
            garage_id=garage.garage_id, floor_number=1,
            floor_name='Ground', total_spots=5, available_spots=4,
        )
        db.session.add(floor)
        db.session.flush()

        spots = []
        for i in range(5):
            status = SpotStatusEnum.occupied if i == 2 else SpotStatusEnum.available
            s = ParkingSpot(
                floor_id=floor.floor_id,
                spot_type=SpotTypeEnum.standard,
                status=status,
                location_reference=f'A-{i + 1:02d}',
            )
            db.session.add(s)
            spots.append(s)
        db.session.flush()

        base = datetime(2026, 3, 20)
        events = [
            (spots[0].spot_id, base.replace(hour=8),  OccupancyChangeEnum.occupied),
            (spots[1].spot_id, base.replace(hour=9),  OccupancyChangeEnum.occupied),
            (spots[2].spot_id, base.replace(hour=10), OccupancyChangeEnum.occupied),
            (spots[0].spot_id, base.replace(hour=14), OccupancyChangeEnum.freed),
            (spots[1].spot_id, base.replace(hour=16), OccupancyChangeEnum.freed),
        ]
        for spot_id, ts, ct in events:
            db.session.add(OccupancyLog(
                spot_id=spot_id, changed_at=ts, change_type=ct,
            ))

        db.session.commit()

        return {
            'floor_id': floor.floor_id,
            'spot_ids': [s.spot_id for s in spots],
            'base': base,
        }


# ======================================================================
#  GET /v1/analytics/utilization
# ======================================================================

class TestUtilization:

    def test_hourly_buckets(self, client, seeded):
        """Range <= 7 days -> hourly interval; verify occupancy math."""
        start = '2026-03-20T00:00:00Z'
        end = '2026-03-20T23:59:59Z'

        resp = client.get(f'/v1/analytics/utilization?start={start}&end={end}')
        assert resp.status_code == 200
        body = resp.get_json()

        assert body['interval'] == 'hourly'
        assert body['totalSpots'] == 5

        buckets = body['buckets']
        assert len(buckets) > 0

        # Find the 10:00 bucket (after 3rd occupied event) -> 3/5 = 60%
        b10 = next((b for b in buckets if b['timestamp'] == '2026-03-20T10:00:00Z'), None)
        assert b10 is not None
        assert b10['occupied'] == 3
        assert b10['utilizationRate'] == 60.0

        # 14:00 bucket (one freed) -> 2/5 = 40%
        b14 = next((b for b in buckets if b['timestamp'] == '2026-03-20T14:00:00Z'), None)
        assert b14 is not None
        assert b14['occupied'] == 2
        assert b14['utilizationRate'] == 40.0

        # 16:00 bucket (another freed) -> 1/5 = 20%
        b16 = next((b for b in buckets if b['timestamp'] == '2026-03-20T16:00:00Z'), None)
        assert b16 is not None
        assert b16['occupied'] == 1
        assert b16['utilizationRate'] == 20.0

    def test_daily_interval_for_long_range(self, client, seeded):
        """Range > 7 days -> daily interval."""
        start = '2026-03-01T00:00:00Z'
        end = '2026-03-25T00:00:00Z'

        resp = client.get(f'/v1/analytics/utilization?start={start}&end={end}')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['interval'] == 'daily'

    def test_floor_filter(self, client, seeded):
        """Supplying floor_id restricts to that floor's spots."""
        fid = seeded['floor_id']
        start = '2026-03-20T00:00:00Z'
        end = '2026-03-20T23:59:59Z'

        resp = client.get(
            f'/v1/analytics/utilization?start={start}&end={end}&floor_id={fid}'
        )
        assert resp.status_code == 200
        assert resp.get_json()['totalSpots'] == 5

    def test_floor_not_found(self, client, seeded):
        start = '2026-03-20T00:00:00Z'
        end = '2026-03-20T23:59:59Z'

        resp = client.get(
            f'/v1/analytics/utilization?start={start}&end={end}&floor_id=9999'
        )
        assert resp.status_code == 404

    def test_empty_log(self, client):
        """No occupancy events -> empty buckets, zero spots."""
        with app.app_context():
            g = Garage(name='Empty', total_capacity=0, number_of_floors=0)
            db.session.add(g)
            db.session.commit()

        start = '2026-01-01T00:00:00Z'
        end = '2026-01-02T00:00:00Z'
        resp = client.get(f'/v1/analytics/utilization?start={start}&end={end}')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['totalSpots'] == 0
        assert body['buckets'] == []

    def test_invalid_range_end_before_start(self, client, seeded):
        resp = client.get(
            '/v1/analytics/utilization?start=2026-03-20T12:00:00Z&end=2026-03-20T06:00:00Z'
        )
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_range'

    def test_missing_start(self, client, seeded):
        resp = client.get('/v1/analytics/utilization?end=2026-03-20T23:00:00Z')
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_range'

    def test_missing_end(self, client, seeded):
        resp = client.get('/v1/analytics/utilization?start=2026-03-20T00:00:00Z')
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_range'


# ======================================================================
#  GET /v1/analytics/occupancy
# ======================================================================

class TestOccupancy:

    def test_live_counts(self, client, seeded):
        """Live occupancy reflects current ParkingSpot status."""
        resp = client.get('/v1/analytics/occupancy')
        assert resp.status_code == 200
        body = resp.get_json()

        live = body['live']
        assert live['total'] == 5
        assert live['occupied'] == 1    # only spot 3 is occupied
        assert live['available'] == 4
        assert live['utilizationRate'] == 20.0

    def test_live_by_floor(self, client, seeded):
        """byFloor breakdown present when no floor_id filter."""
        resp = client.get('/v1/analytics/occupancy')
        body = resp.get_json()

        assert 'byFloor' in body['live']
        floors = body['live']['byFloor']
        assert len(floors) == 1
        assert floors[0]['floorName'] == 'Ground'
        assert floors[0]['total'] == 5

    def test_floor_filter_hides_breakdown(self, client, seeded):
        """When floor_id is given, byFloor is omitted."""
        fid = seeded['floor_id']
        resp = client.get(f'/v1/analytics/occupancy?floor_id={fid}')
        body = resp.get_json()

        assert body['live']['total'] == 5
        assert 'byFloor' not in body['live']

    def test_trend_present(self, client, seeded):
        """Historical trend is returned."""
        resp = client.get('/v1/analytics/occupancy')
        body = resp.get_json()
        assert 'trend' in body
        assert isinstance(body['trend'], list)

    def test_empty_garage(self, client):
        """No spots at all -> zero counts, empty trend."""
        resp = client.get('/v1/analytics/occupancy')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['live']['total'] == 0
        assert body['live']['occupied'] == 0
        assert body['trend'] == []


# ======================================================================
#  GET /v1/analytics/peak-hours
# ======================================================================

class TestPeakHours:

    def test_ranking(self, client, seeded):
        """Hours are ranked by average entries descending."""
        start = '2026-03-20T00:00:00Z'
        end = '2026-03-20T23:59:59Z'

        resp = client.get(f'/v1/analytics/peak-hours?start={start}&end={end}')
        assert resp.status_code == 200
        body = resp.get_json()

        hours = body['hours']
        assert len(hours) == 24

        # Rank 1 should have the highest averageEntries
        assert hours[0]['rank'] == 1
        for i in range(len(hours) - 1):
            assert hours[i]['averageEntries'] >= hours[i + 1]['averageEntries']

    def test_entry_counts(self, client, seeded):
        """3 occupied events at hours 8, 9, 10 -> each has totalEntries 1."""
        start = '2026-03-20T00:00:00Z'
        end = '2026-03-20T23:59:59Z'

        resp = client.get(f'/v1/analytics/peak-hours?start={start}&end={end}')
        hours_by_h = {h['hour']: h for h in resp.get_json()['hours']}

        assert hours_by_h[8]['totalEntries'] == 1
        assert hours_by_h[9]['totalEntries'] == 1
        assert hours_by_h[10]['totalEntries'] == 1
        # hour 14 and 16 are "freed" events, not "occupied" -> 0 entries
        assert hours_by_h[14]['totalEntries'] == 0
        assert hours_by_h[16]['totalEntries'] == 0

    def test_empty_log(self, client):
        """No events -> all hours have 0 entries."""
        start = '2026-01-01T00:00:00Z'
        end = '2026-01-02T00:00:00Z'

        resp = client.get(f'/v1/analytics/peak-hours?start={start}&end={end}')
        assert resp.status_code == 200
        hours = resp.get_json()['hours']
        assert all(h['totalEntries'] == 0 for h in hours)

    def test_invalid_range(self, client):
        resp = client.get(
            '/v1/analytics/peak-hours?start=2026-03-20T12:00:00Z&end=2026-03-20T06:00:00Z'
        )
        assert resp.status_code == 400
        assert resp.get_json()['error'] == 'invalid_range'

    def test_num_days(self, client, seeded):
        """numDays reflects the range span."""
        start = '2026-03-18T00:00:00Z'
        end = '2026-03-21T00:00:00Z'

        resp = client.get(f'/v1/analytics/peak-hours?start={start}&end={end}')
        assert resp.get_json()['numDays'] == 3
