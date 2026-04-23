"""
routes/analytics.py — GarageFlow Analytics Blueprint

Endpoints:
  GET /v1/analytics/utilization  — Utilization rate over time
  GET /v1/analytics/occupancy    — Live occupancy + historical trends
  GET /v1/analytics/peak-hours   — Peak usage time analysis

All endpoints are read-only. No writes to the database.
Driven entirely from the occupancy_log table, cross-referenced with
parking_spot and floor for per-floor breakdowns.
"""

from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from sqlalchemy import case, func

from utils import login_required

analytics_bp = Blueprint('analytics', __name__, url_prefix='/v1/analytics')


# ------------------------------------------------------------------
#  Shared helpers
# ------------------------------------------------------------------

def _parse_range():
    """
    Parse required date-range query params as naive-UTC datetimes.
    Accepts ?start/?end or ?from/?to (the latter for frontend compat).
    Raises ValueError on missing, malformed, or reversed dates.
    """
    raw_start = request.args.get('start') or request.args.get('from')
    raw_end = request.args.get('end') or request.args.get('to')

    if not raw_start or not raw_end:
        missing = 'start' if not raw_start else 'end'
        raise ValueError(f'{missing} query parameter is required')

    try:
        start_dt = datetime.fromisoformat(
            raw_start.replace('Z', '+00:00')
        ).replace(tzinfo=None)
    except (ValueError, AttributeError):
        raise ValueError('start is not a valid ISO 8601 datetime')

    try:
        end_dt = datetime.fromisoformat(
            raw_end.replace('Z', '+00:00')
        ).replace(tzinfo=None)
    except (ValueError, AttributeError):
        raise ValueError('end is not a valid ISO 8601 datetime')

    if end_dt <= start_dt:
        raise ValueError('end must be after start')

    return start_dt, end_dt


# ------------------------------------------------------------------
#  GET /v1/analytics/utilization
# ------------------------------------------------------------------

@analytics_bp.route('/utilization', methods=['GET'])
@login_required
def utilization():
    """
    Utilization rate over time: (occupied / total) x 100, grouped by
    time interval.  Hourly buckets when range <= 7 days, daily otherwise.

    Query params:
      start     (required)  ISO 8601
      end       (required)  ISO 8601
      floor_id  (optional)  integer — restrict to a single floor
    """
    from app import db
    from models import OccupancyLog, OccupancyChangeEnum, ParkingSpot, Floor

    try:
        start_dt, end_dt = _parse_range()
    except ValueError as exc:
        return jsonify({'error': 'invalid_range', 'message': str(exc)}), 400

    floor_id = request.args.get('floor_id', type=int)
    if floor_id is not None and not Floor.query.get(floor_id):
        return jsonify({'error': 'floor_not_found',
                        'message': 'No floor found with that ID'}), 404

    garage_id = request.args.get('garage_id', type=int)

    # Total spots for the denominator
    total_q = db.session.query(func.count(ParkingSpot.spot_id))
    if floor_id is not None:
        total_q = total_q.filter(ParkingSpot.floor_id == floor_id)
    elif garage_id is not None:
        total_q = total_q.join(Floor, ParkingSpot.floor_id == Floor.floor_id).filter(Floor.garage_id == garage_id)
    total_spots = total_q.scalar() or 0

    range_days = (end_dt - start_dt).days
    use_daily = range_days > 7
    interval = 'daily' if use_daily else 'hourly'

    if total_spots == 0:
        return jsonify({
            'start': start_dt.isoformat() + 'Z',
            'end': end_dt.isoformat() + 'Z',
            'interval': interval,
            'totalSpots': 0,
            'buckets': [],
        }), 200

    # --- SQL: initial occupancy before the window ----------------------
    # For each spot, find the most recent event before `start`.
    # Count how many of those are "occupied".
    latest_sub = db.session.query(
        OccupancyLog.spot_id,
        func.max(OccupancyLog.changed_at).label('latest'),
    ).filter(OccupancyLog.changed_at < start_dt)
    if floor_id is not None:
        latest_sub = latest_sub.join(
            ParkingSpot, OccupancyLog.spot_id == ParkingSpot.spot_id,
        ).filter(ParkingSpot.floor_id == floor_id)
    latest_sub = latest_sub.group_by(OccupancyLog.spot_id).subquery()

    initial_occupied = db.session.query(func.count()).select_from(
        OccupancyLog,
    ).join(
        latest_sub,
        db.and_(
            OccupancyLog.spot_id == latest_sub.c.spot_id,
            OccupancyLog.changed_at == latest_sub.c.latest,
        ),
    ).filter(
        OccupancyLog.change_type == OccupancyChangeEnum.occupied,
    ).scalar() or 0

    # --- SQL: bucket-level aggregation ---------------------------------
    if use_daily:
        bucket_expr = func.strftime('%Y-%m-%d', OccupancyLog.changed_at)
    else:
        bucket_expr = func.strftime('%Y-%m-%dT%H:00:00', OccupancyLog.changed_at)

    q = db.session.query(
        bucket_expr.label('bucket'),
        func.sum(case(
            (OccupancyLog.change_type == OccupancyChangeEnum.occupied, 1),
            else_=0,
        )).label('entries'),
        func.sum(case(
            (OccupancyLog.change_type == OccupancyChangeEnum.freed, 1),
            else_=0,
        )).label('exits'),
    ).filter(
        OccupancyLog.changed_at >= start_dt,
        OccupancyLog.changed_at <= end_dt,
    )
    if floor_id is not None:
        q = q.join(
            ParkingSpot, OccupancyLog.spot_id == ParkingSpot.spot_id,
        ).filter(ParkingSpot.floor_id == floor_id)

    rows = q.group_by('bucket').order_by('bucket').all()

    # --- Build response (loop over buckets, not individual events) -----
    occupied = initial_occupied
    buckets = []
    for bucket_key, entries, exits in rows:
        occupied = max(0, occupied + (entries or 0) - (exits or 0))
        rate = round((occupied / total_spots) * 100, 2)
        if 'T' in bucket_key:
            ts = bucket_key + 'Z'
        else:
            ts = bucket_key + 'T00:00:00Z'
        buckets.append({
            'timestamp': ts,
            'occupied': occupied,
            'total': total_spots,
            'utilizationRate': rate,
        })

    return jsonify({
        'start': start_dt.isoformat() + 'Z',
        'end': end_dt.isoformat() + 'Z',
        'interval': interval,
        'totalSpots': total_spots,
        'buckets': buckets,
    }), 200


# ------------------------------------------------------------------
#  GET /v1/analytics/occupancy
# ------------------------------------------------------------------

@analytics_bp.route('/occupancy', methods=['GET'])
@login_required
def occupancy():
    """
    Live occupancy from current spot status, plus a 30-day historical
    trend built from occupancy_log.

    Query params:
      floor_id  (optional)  integer — restrict to a single floor
    """
    from app import db
    from models import (
        OccupancyLog, OccupancyChangeEnum, ParkingSpot, SpotStatusEnum, Floor,
    )

    floor_id = request.args.get('floor_id', type=int)
    if floor_id is not None and not Floor.query.get(floor_id):
        return jsonify({'error': 'floor_not_found',
                        'message': 'No floor found with that ID'}), 404

    # --- Live counts ---------------------------------------------------
    total_q = ParkingSpot.query
    occupied_q = ParkingSpot.query.filter_by(status=SpotStatusEnum.occupied)
    if floor_id is not None:
        total_q = total_q.filter_by(floor_id=floor_id)
        occupied_q = occupied_q.filter_by(floor_id=floor_id)

    total_count = total_q.count()
    occupied_count = occupied_q.count()
    available_count = total_count - occupied_count
    live_rate = round((occupied_count / total_count) * 100, 2) if total_count else 0.0

    # Per-floor breakdown (skip when a single floor was requested)
    by_floor = []
    if floor_id is None:
        floor_q = Floor.query.order_by(Floor.floor_number)
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            floor_q = floor_q.filter(Floor.garage_id == garage_id)
        floors = floor_q.all()
        for f in floors:
            occ = f.total_spots - f.available_spots
            by_floor.append({
                'floorId': f.floor_id,
                'floorName': f.floor_name or f'Floor {f.floor_number}',
                'occupied': occ,
                'total': f.total_spots,
                'available': f.available_spots,
            })

    # --- 30-day historical trend (SQL aggregation) ---------------------
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)

    trend_q = db.session.query(
        func.strftime('%Y-%m-%d', OccupancyLog.changed_at).label('day'),
        func.sum(case(
            (OccupancyLog.change_type == OccupancyChangeEnum.occupied, 1),
            else_=0,
        )).label('entries'),
        func.sum(case(
            (OccupancyLog.change_type == OccupancyChangeEnum.freed, 1),
            else_=0,
        )).label('exits'),
    ).filter(OccupancyLog.changed_at >= thirty_days_ago)

    if floor_id is not None:
        trend_q = trend_q.join(
            ParkingSpot, OccupancyLog.spot_id == ParkingSpot.spot_id,
        ).filter(ParkingSpot.floor_id == floor_id)

    trend_rows = trend_q.group_by('day').order_by('day').all()

    # Initial state before the 30-day window
    init_sub = db.session.query(
        OccupancyLog.spot_id,
        func.max(OccupancyLog.changed_at).label('latest'),
    ).filter(OccupancyLog.changed_at < thirty_days_ago)
    if floor_id is not None:
        init_sub = init_sub.join(
            ParkingSpot, OccupancyLog.spot_id == ParkingSpot.spot_id,
        ).filter(ParkingSpot.floor_id == floor_id)
    init_sub = init_sub.group_by(OccupancyLog.spot_id).subquery()

    init_occ = db.session.query(func.count()).select_from(
        OccupancyLog,
    ).join(
        init_sub,
        db.and_(
            OccupancyLog.spot_id == init_sub.c.spot_id,
            OccupancyLog.changed_at == init_sub.c.latest,
        ),
    ).filter(
        OccupancyLog.change_type == OccupancyChangeEnum.occupied,
    ).scalar() or 0

    ref_total = total_count or 1
    running = init_occ
    trend = []
    for day, entries, exits in trend_rows:
        running = max(0, running + (entries or 0) - (exits or 0))
        trend.append({
            'date': day,
            'occupied': running,
            'utilizationRate': round((running / ref_total) * 100, 2),
        })

    result = {
        'live': {
            'occupied': occupied_count,
            'total': total_count,
            'available': available_count,
            'utilizationRate': live_rate,
        },
        'trend': trend,
    }
    if by_floor:
        result['live']['byFloor'] = by_floor

    return jsonify(result), 200


# ------------------------------------------------------------------
#  GET /v1/analytics/peak-hours
# ------------------------------------------------------------------

@analytics_bp.route('/peak-hours', methods=['GET'])
@login_required
def peak_hours():
    """
    Peak usage: group occupancy "occupied" events from occupancy_log
    by hour of day (0-23), compute average daily load, rank descending.

    Query params:
      start  (required)  ISO 8601
      end    (required)  ISO 8601
    """
    from app import db
    from models import OccupancyLog, OccupancyChangeEnum

    try:
        start_dt, end_dt = _parse_range()
    except ValueError as exc:
        return jsonify({'error': 'invalid_range', 'message': str(exc)}), 400

    num_days = max((end_dt - start_dt).days, 1)

    # SQL aggregation: count occupied events grouped by hour-of-day
    peak_q = db.session.query(
        func.cast(func.strftime('%H', OccupancyLog.changed_at), db.Integer).label('hour'),
        func.count().label('total_entries'),
    ).filter(
        OccupancyLog.changed_at >= start_dt,
        OccupancyLog.changed_at <= end_dt,
        OccupancyLog.change_type == OccupancyChangeEnum.occupied,
    )
    garage_id = request.args.get('garage_id', type=int)
    if garage_id is not None:
        from models import ParkingSpot, Floor
        peak_q = (peak_q
                  .join(ParkingSpot, OccupancyLog.spot_id == ParkingSpot.spot_id)
                  .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
                  .filter(Floor.garage_id == garage_id))
    rows = peak_q.group_by('hour').all()

    counts = {h: 0 for h in range(24)}
    for hour, total in rows:
        counts[int(hour)] = total

    hours = []
    for h in range(24):
        hours.append({
            'hour': h,
            'totalEntries': counts[h],
            'averageEntries': round(counts[h] / num_days, 2),
        })

    # Rank by average entries descending (stable sort by hour as tiebreak)
    hours.sort(key=lambda x: (-x['averageEntries'], x['hour']))
    for i, entry in enumerate(hours):
        entry['rank'] = i + 1

    return jsonify({
        'start': start_dt.isoformat() + 'Z',
        'end': end_dt.isoformat() + 'Z',
        'numDays': num_days,
        'hours': hours,
    }), 200
