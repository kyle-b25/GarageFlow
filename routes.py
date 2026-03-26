"""
routes.py — GarageFlow Public API Blueprint

The four endpoints the operator kiosk frontend depends on:
  POST /v1/tickets
  POST /v1/reservations
  GET  /v1/reservations
  GET  /v1/floors

No authentication required.  Registered in app.py as v1_bp.
"""

import re
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

v1_bp = Blueprint('v1', __name__, url_prefix='/v1')


# ------------------------------------------------------------------
#  Shared constants
# ------------------------------------------------------------------

_DRIVER_CLASS_TO_SPOT_TYPE = {
    'standard':      'standard',
    'accessibility': 'accessibility',
    'employee':      'staff',
    'eco':           'standard',
}

VALID_DRIVER_CLASSES = set(_DRIVER_CLASS_TO_SPOT_TYPE.keys())

_STATUS_MAP = {}  # populated on first use (deferred import)


def _get_status_map():
    global _STATUS_MAP
    if not _STATUS_MAP:
        from models import ReservationStatusEnum
        _STATUS_MAP = {
            ReservationStatusEnum.confirmed: 'confirmed',
            ReservationStatusEnum.fulfilled: 'complete',
            ReservationStatusEnum.expired:   'expired',
            ReservationStatusEnum.cancelled: 'cancelled',
        }
    return _STATUS_MAP


# ------------------------------------------------------------------
#  Helpers
# ------------------------------------------------------------------

def assign_spot(driver_class):
    """
    Find the best available (spot, floor) pair for the given driver class.

    Floors are sorted by availability ratio (available/total) descending,
    tiebreak by floor_number ascending.

    Returns (spot, floor) on success, or (None, None) if garage is full.
    """
    from models import Floor, ParkingSpot, SpotTypeEnum, SpotStatusEnum

    spot_type_val = SpotTypeEnum(_DRIVER_CLASS_TO_SPOT_TYPE[driver_class])

    floors = [f for f in Floor.query.filter(Floor.available_spots > 0).all()
              if f.total_spots > 0]
    if not floors:
        return None, None

    floors.sort(key=lambda f: (-(f.available_spots / f.total_spots), f.floor_number))

    for floor in floors:
        spot = ParkingSpot.query.filter_by(
            floor_id=floor.floor_id,
            spot_type=spot_type_val,
            status=SpotStatusEnum.available,
        ).first()
        if spot:
            return spot, floor

    return None, None


def _log_error(source, description):
    """Append a row to SystemEvent for auditing failures."""
    try:
        from app import db
        from models import SystemEvent
        db.session.add(SystemEvent(source=source, description=description))
        db.session.commit()
    except Exception:
        pass  # best-effort logging


# ------------------------------------------------------------------
#  POST /v1/tickets
# ------------------------------------------------------------------

@v1_bp.route('/tickets', methods=['POST'])
def post_ticket():
    from app import db
    from models import (
        Floor, Vehicle, Ticket, OccupancyLog,
        SpotStatusEnum, VehicleTypeEnum, TicketStatusEnum, OccupancyChangeEnum,
    )

    data = request.get_json(silent=True) or {}

    license_plate = data.get('licensePlate')
    driver_class  = data.get('driverClass')
    phone         = data.get('phone')

    # Validate input
    if not license_plate or not driver_class:
        missing = 'licensePlate' if not license_plate else 'driverClass'
        return jsonify({'error': 'missing_required_field', 'message': f'{missing} is required'}), 400

    if driver_class not in VALID_DRIVER_CLASSES:
        return jsonify({
            'error': 'missing_required_field',
            'message': f'driverClass must be one of: {", ".join(sorted(VALID_DRIVER_CLASSES))}',
        }), 400

    # Check global availability
    if not Floor.query.filter(Floor.available_spots > 0).first():
        return jsonify({'error': 'garage_full', 'message': 'Garage is full'}), 503

    # Get or create Vehicle
    vehicle = Vehicle.query.filter_by(license_plate=license_plate).first()
    if not vehicle:
        vehicle = Vehicle(
            license_plate=license_plate,
            vehicle_type=VehicleTypeEnum.car,
            customer_id=None,
        )
        db.session.add(vehicle)
        db.session.flush()

    # Duplicate active ticket check
    if Ticket.query.filter_by(vehicle_id=vehicle.vehicle_id, status=TicketStatusEnum.active).first():
        return jsonify({'error': 'duplicate_plate', 'message': 'Vehicle already has an active ticket'}), 409

    # Assign spot
    spot, floor = assign_spot(driver_class)
    if not spot:
        return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

    # Create Ticket + mark spot occupied + OccupancyLog
    ticket = Ticket(
        vehicle_id=vehicle.vehicle_id,
        spot_id=spot.spot_id,
        entry_timestamp=datetime.utcnow(),
        status=TicketStatusEnum.active,
    )

    try:
        db.session.add(ticket)
        db.session.flush()
        spot.status = SpotStatusEnum.occupied
        floor.available_spots -= 1
        db.session.add(OccupancyLog(
            spot_id=spot.spot_id,
            changed_at=datetime.utcnow(),
            change_type=OccupancyChangeEnum.occupied,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_error('routes.post_ticket', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create ticket'}), 500

    response = {
        'ticketId':      ticket.ticket_id,
        'licensePlate':  license_plate,
        'assignedFloor': floor.floor_number,
        'entryTime':     ticket.entry_timestamp.isoformat() + 'Z',
        'status':        'active',
    }
    if phone:
        response['phone'] = phone

    return jsonify(response), 201


# ------------------------------------------------------------------
#  POST /v1/reservations
# ------------------------------------------------------------------

@v1_bp.route('/reservations', methods=['POST'])
def post_reservation():
    from app import db
    from models import Reservation, ReservationStatusEnum

    data = request.get_json(silent=True) or {}

    phone             = data.get('phone')
    scheduled_arrival = data.get('scheduledArrival')
    driver_class      = data.get('driverClass')

    # Validate input
    if not phone or not scheduled_arrival:
        missing = 'phone' if not phone else 'scheduledArrival'
        return jsonify({'error': 'missing_required_field', 'message': f'{missing} is required'}), 400

    try:
        parsed_arrival = datetime.fromisoformat(scheduled_arrival.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return jsonify({'error': 'invalid_scheduled_arrival', 'message': 'scheduledArrival is not a valid ISO 8601 datetime'}), 400

    now = datetime.now(timezone.utc)
    if parsed_arrival <= now:
        return jsonify({'error': 'invalid_scheduled_arrival', 'message': 'scheduledArrival must be in the future'}), 400

    # Find advisory floor
    effective_class = driver_class if driver_class in _DRIVER_CLASS_TO_SPOT_TYPE else 'standard'
    spot, floor = assign_spot(effective_class)
    if not spot:
        return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

    # Create Reservation (do NOT mark spot occupied)
    reservation = Reservation(
        phone=phone,
        start_datetime=parsed_arrival.replace(tzinfo=None),
        customer_id=None,
        vehicle_id=None,
        floor_number=floor.floor_number,
        status=ReservationStatusEnum.confirmed,
    )
    try:
        db.session.add(reservation)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_error('routes.post_reservation', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create reservation'}), 500

    return jsonify({
        'reservationId':    f'R-{reservation.reservation_id:04d}',
        'assignedFloor':    floor.floor_number,
        'scheduledArrival': scheduled_arrival,
        'status':           'confirmed',
    }), 201


# ------------------------------------------------------------------
#  GET /v1/reservations
# ------------------------------------------------------------------

@v1_bp.route('/reservations', methods=['GET'])
def get_reservations():
    from models import Reservation, ReservationStatusEnum

    phone = request.args.get('phone')
    if not phone:
        return jsonify({'error': 'missing_required_field', 'message': 'phone is required'}), 400

    include_old = request.args.get('includeOld', 'false').lower() == 'true'

    q = Reservation.query.filter_by(phone=phone)
    if not include_old:
        q = q.filter_by(status=ReservationStatusEnum.confirmed)
    reservations = q.order_by(Reservation.start_datetime).all()

    status_map = _get_status_map()
    result = [
        {
            'reservationId':    f'R-{r.reservation_id:04d}',
            'assignedFloor':    r.floor_number if r.floor_number is not None else -1,
            'scheduledArrival': r.start_datetime.isoformat() + 'Z',
            'status':           status_map[r.status],
        }
        for r in reservations
    ]
    return jsonify(result), 200


# ------------------------------------------------------------------
#  GET /v1/floors
# ------------------------------------------------------------------

@v1_bp.route('/floors', methods=['GET'])
def get_floors():
    from models import Floor, SpotStatusEnum

    floors = Floor.query.order_by(Floor.floor_number).all()
    result = []

    for floor in floors:
        zone_available = {}
        zone_total     = {}

        for spot in floor.parking_spots:
            loc = spot.location_reference
            if not loc:
                continue
            m    = re.search(r'[A-Z]', loc)
            zone = m.group(0) if m else '_unzoned'
            zone_total[zone] = zone_total.get(zone, 0) + 1
            if spot.status == SpotStatusEnum.available:
                zone_available[zone] = zone_available.get(zone, 0) + 1

        zones = {
            z: (zone_available.get(z, 0) if zone_available.get(z, 0) > 0 else 'Full')
            for z in sorted(zone_total)
        }

        result.append({
            'floor': floor.floor_name if floor.floor_name else f'Floor {floor.floor_number}',
            'total': floor.available_spots,
            'zones': zones,
        })

    return jsonify(result), 200


# ------------------------------------------------------------------
#  GET /v1/capacity
# ------------------------------------------------------------------

@v1_bp.route('/capacity', methods=['GET'])
def get_capacity():
    from app import db
    from models import ParkingSpot, SpotTypeEnum, SpotStatusEnum

    try:
        spots = ParkingSpot.query.all()

        by_type = {}
        for st in SpotTypeEnum:
            by_type[st.value] = {'total': 0, 'occupied': 0, 'available': 0}

        for spot in spots:
            bucket = by_type[spot.spot_type.value]
            bucket['total'] += 1
            if spot.status == SpotStatusEnum.occupied:
                bucket['occupied'] += 1
            elif spot.status == SpotStatusEnum.available:
                bucket['available'] += 1

        total    = sum(b['total'] for b in by_type.values())
        occupied = sum(b['occupied'] for b in by_type.values())
        available = sum(b['available'] for b in by_type.values())

        return jsonify({
            'total': total,
            'occupied': occupied,
            'available': available,
            'byType': by_type,
        }), 200
    except Exception as exc:
        db.session.rollback()
        _log_error('routes.get_capacity', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ------------------------------------------------------------------
#  GET /v1/capacity/status
# ------------------------------------------------------------------

@v1_bp.route('/capacity/status', methods=['GET'])
def get_capacity_status():
    from app import db
    from models import ParkingSpot, SpotTypeEnum, SpotStatusEnum

    try:
        spots = ParkingSpot.query.filter_by(status=SpotStatusEnum.available).all()

        counts = {st.value: 0 for st in SpotTypeEnum}
        for spot in spots:
            counts[spot.spot_type.value] += 1

        return jsonify(counts), 200
    except Exception as exc:
        db.session.rollback()
        _log_error('routes.get_capacity_status', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ------------------------------------------------------------------
#  GET /v1/capacity/floors/<floorId>
# ------------------------------------------------------------------

@v1_bp.route('/capacity/floors/<int:floorId>', methods=['GET'])
def get_capacity_floor(floorId):
    from app import db
    from models import Floor, ParkingSpot, SpotTypeEnum, SpotStatusEnum

    try:
        floor = Floor.query.filter_by(floor_id=floorId).first()
        if not floor:
            return jsonify({'error': 'floor_not_found'}), 404

        spots = ParkingSpot.query.filter_by(floor_id=floorId).all()

        by_type = {}
        for st in SpotTypeEnum:
            by_type[st.value] = {'total': 0, 'occupied': 0, 'available': 0}

        for spot in spots:
            bucket = by_type[spot.spot_type.value]
            bucket['total'] += 1
            if spot.status == SpotStatusEnum.occupied:
                bucket['occupied'] += 1
            elif spot.status == SpotStatusEnum.available:
                bucket['available'] += 1

        total    = sum(b['total'] for b in by_type.values())
        occupied = sum(b['occupied'] for b in by_type.values())
        available = sum(b['available'] for b in by_type.values())

        return jsonify({
            'floorId': floor.floor_id,
            'floorName': floor.floor_name or f'Floor {floor.floor_number}',
            'total': total,
            'occupied': occupied,
            'available': available,
            'byType': by_type,
        }), 200
    except Exception as exc:
        db.session.rollback()
        _log_error('routes.get_capacity_floor', str(exc))
        return jsonify({'error': 'server_error'}), 500
