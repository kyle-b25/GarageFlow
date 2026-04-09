"""
reservations_bp.py — GarageFlow Reservations API Blueprint

CRUD endpoints for reservations, plus check-in conversion to active ticket.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

from app import db
from models import (
    Reservation, ReservationStatusEnum,
    Vehicle, Customer, VehicleTypeEnum,
    Ticket, TicketStatusEnum,
    OccupancyLog, OccupancyChangeEnum,
    SpotStatusEnum,
)
from utils import assign_spot, log_error, _DRIVER_CLASS_TO_SPOT_TYPE, VALID_DRIVER_CLASSES

reservations_bp = Blueprint('reservations', __name__, url_prefix='/v1/reservations')


# ── State Machine ────────────────────────────────────────────────

ALLOWED_TRANSITIONS = {
    ReservationStatusEnum.confirmed: [
        ReservationStatusEnum.cancelled,
        ReservationStatusEnum.fulfilled,
        ReservationStatusEnum.expired,
    ],
    ReservationStatusEnum.cancelled: [],
    ReservationStatusEnum.fulfilled: [],
    ReservationStatusEnum.expired:   [],
}


def _parse_reservation_id(raw):
    """Parse 'R-0001' or '1' into an integer reservation ID, or None."""
    if isinstance(raw, str) and raw.upper().startswith('R-'):
        raw = raw[2:]
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


# ── Serializer ────────────────────────────────────────────────────

STATUS_LABELS = {
    ReservationStatusEnum.confirmed: 'confirmed',
    ReservationStatusEnum.fulfilled: 'fulfilled',
    ReservationStatusEnum.expired:   'expired',
    ReservationStatusEnum.cancelled: 'cancelled',
}


def _reservation_json(r):
    return {
        'reservationId':    f'R-{r.reservation_id:04d}',
        'phone':            r.phone,
        'assignedFloor':    r.floor_number if r.floor_number is not None else -1,
        'scheduledArrival': r.start_datetime.isoformat() + 'Z',
        'status':           STATUS_LABELS[r.status],
        'quotedFee':        float(r.quoted_fee) if r.quoted_fee is not None else None,
        'vehicleId':        r.vehicle_id,
        'customerId':       r.customer_id,
    }


# ── POST /v1/reservations ────────────────────────────────────────

@reservations_bp.route('', methods=['POST'])
def post_reservation():
    data = request.get_json(silent=True) or {}

    phone             = data.get('phone')
    scheduled_arrival = data.get('scheduledArrival')
    driver_class      = data.get('driverClass')
    license_plate     = data.get('licensePlate')
    vehicle_id        = data.get('vehicleId')

    if not phone or not scheduled_arrival:
        missing = 'phone' if not phone else 'scheduledArrival'
        return jsonify({'error': 'missing_required_field', 'message': f'{missing} is required'}), 400

    # Require licensePlate or vehicleId
    if not license_plate and not vehicle_id:
        return jsonify({'error': 'missing_required_field', 'message': 'licensePlate or vehicleId is required'}), 400

    try:
        parsed_arrival = datetime.fromisoformat(scheduled_arrival.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return jsonify({'error': 'invalid_scheduled_arrival', 'message': 'scheduledArrival is not a valid ISO 8601 datetime'}), 400

    now = datetime.utcnow()
    if parsed_arrival.replace(tzinfo=None) <= now:
        return jsonify({'error': 'invalid_scheduled_arrival', 'message': 'scheduledArrival must be in the future'}), 400

    # Resolve vehicle
    vehicle = None
    if vehicle_id:
        vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle and license_plate:
        vehicle = Vehicle.query.filter_by(license_plate=license_plate).first()
        if not vehicle:
            vehicle = Vehicle(
                license_plate=license_plate,
                vehicle_type=VehicleTypeEnum.car,
            )
            db.session.add(vehicle)
            db.session.flush()

    if driver_class and driver_class not in _DRIVER_CLASS_TO_SPOT_TYPE:
        return jsonify({
            'error': 'invalid_driver_class',
            'message': f'driverClass must be one of: {", ".join(sorted(_DRIVER_CLASS_TO_SPOT_TYPE.keys()))}',
        }), 400
    effective_class = driver_class if driver_class in _DRIVER_CLASS_TO_SPOT_TYPE else 'standard'

    try:
        spot, floor = assign_spot(effective_class, arrival_datetime=parsed_arrival.replace(tzinfo=None))
    except Exception as exc:
        db.session.rollback()
        log_error('reservations.post_reservation', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to assign spot'}), 500

    if not spot:
        return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

    reservation = Reservation(
        phone=phone,
        driver_class=effective_class,
        start_datetime=parsed_arrival.replace(tzinfo=None),
        end_datetime=None,
        customer_id=data.get('customerId'),
        vehicle_id=vehicle.vehicle_id if vehicle else None,
        floor_number=floor.floor_number,
        quoted_fee=data.get('quotedFee'),
        status=ReservationStatusEnum.confirmed,
    )
    try:
        db.session.add(reservation)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('reservations.post_reservation', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create reservation'}), 500

    return jsonify(_reservation_json(reservation)), 201


# ── GET /v1/reservations ──────────────────────────────────────────

@reservations_bp.route('', methods=['GET'])
def list_reservations():
    try:
        plate_param      = request.args.get('plate')
        phone_param      = request.args.get('phone')
        include_old      = request.args.get('includeOld', 'false').lower() == 'true'

        q = Reservation.query

        if plate_param:
            vehicle = Vehicle.query.filter_by(license_plate=plate_param).first()
            if not vehicle:
                return jsonify([]), 200
            q = q.filter(Reservation.vehicle_id == vehicle.vehicle_id)
        elif phone_param:
            q = q.filter(Reservation.phone == phone_param)

        if not include_old:
            q = q.filter(Reservation.status == ReservationStatusEnum.confirmed)

        reservations = q.order_by(Reservation.start_datetime).all()
        return jsonify([_reservation_json(r) for r in reservations]), 200
    except Exception as exc:
        db.session.rollback()
        log_error('reservations.list_reservations', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ── GET /v1/reservations/<id> ─────────────────────────────────────

@reservations_bp.route('/<reservation_id>', methods=['GET'])
def get_reservation(reservation_id):
    try:
        rid = _parse_reservation_id(reservation_id)
        if rid is None:
            return jsonify({'error': 'invalid_reservation_id'}), 400
        r = Reservation.query.get(rid)
        if not r:
            return jsonify({'error': 'reservation_not_found'}), 404
        return jsonify(_reservation_json(r)), 200
    except Exception:
        return jsonify({'error': 'server_error'}), 500


# ── PUT /v1/reservations/<id> ─────────────────────────────────────

@reservations_bp.route('/<reservation_id>', methods=['PUT'])
def update_reservation(reservation_id):
    try:
        rid = _parse_reservation_id(reservation_id)
        if rid is None:
            return jsonify({'error': 'invalid_reservation_id'}), 400
        r = Reservation.query.get(rid)
        if not r:
            return jsonify({'error': 'reservation_not_found'}), 404

        data = request.get_json(silent=True) or {}

        if 'scheduledArrival' in data:
            try:
                r.start_datetime = datetime.fromisoformat(
                    data['scheduledArrival'].replace('Z', '+00:00')
                ).replace(tzinfo=None)
            except (ValueError, AttributeError):
                return jsonify({'error': 'invalid_scheduled_arrival'}), 400
        if 'endDatetime' in data:
            try:
                r.end_datetime = datetime.fromisoformat(
                    data['endDatetime'].replace('Z', '+00:00')
                ).replace(tzinfo=None)
            except (ValueError, AttributeError):
                return jsonify({'error': 'invalid_end_datetime'}), 400
        if 'status' in data:
            try:
                new_status = ReservationStatusEnum(data['status'])
            except ValueError:
                return jsonify({'error': 'invalid_status'}), 400
            # Validate state transition
            allowed = ALLOWED_TRANSITIONS.get(r.status, [])
            if new_status not in allowed:
                return jsonify({'error': 'invalid_status_transition'}), 400
            r.status = new_status
        if 'quotedFee' in data:
            r.quoted_fee = data['quotedFee']
        if 'floorNumber' in data:
            r.floor_number = data['floorNumber']
        if 'vehicleId' in data:
            r.vehicle_id = data['vehicleId']
        if 'customerId' in data:
            r.customer_id = data['customerId']

        db.session.commit()
        return jsonify(_reservation_json(r)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('reservations.update_reservation', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ── DELETE /v1/reservations/<id> (cancel) ─────────────────────────

@reservations_bp.route('/<reservation_id>', methods=['DELETE'])
def cancel_reservation(reservation_id):
    try:
        rid = _parse_reservation_id(reservation_id)
        if rid is None:
            return jsonify({'error': 'invalid_reservation_id'}), 400
        r = Reservation.query.get(rid)
        if not r:
            return jsonify({'error': 'reservation_not_found'}), 404

        data = request.get_json(silent=True) or {}
        license_plate = data.get('licensePlate')
        phone = data.get('phone')

        if not license_plate or not phone:
            return jsonify({'error': 'missing_required_field'}), 400

        # Verify identity against linked Vehicle and Customer
        vehicle = Vehicle.query.get(r.vehicle_id) if r.vehicle_id else None
        customer = Customer.query.get(r.customer_id) if r.customer_id else None

        plate_match = vehicle and vehicle.license_plate == license_plate
        phone_match = (customer and customer.phone_number == phone) or (r.phone == phone)

        if not plate_match or not phone_match:
            return jsonify({'error': 'verification_failed'}), 403

        r.status = ReservationStatusEnum.cancelled

        # Purge personal data only if customer has no other active reservations
        if customer:
            other_active = Reservation.query.filter(
                Reservation.customer_id == customer.customer_id,
                Reservation.reservation_id != r.reservation_id,
                Reservation.status == ReservationStatusEnum.confirmed,
            ).first()
            if not other_active:
                customer.phone_number = None
                customer.name = None

        db.session.commit()
        return jsonify(_reservation_json(r)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('reservations.cancel_reservation', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ── PUT /v1/reservations/<id>/check (check-in) ───────────────────

@reservations_bp.route('/<reservation_id>/check', methods=['PUT'])
def check_in_reservation(reservation_id):
    try:
        rid = _parse_reservation_id(reservation_id)
        if rid is None:
            return jsonify({'error': 'invalid_reservation_id'}), 400
        r = Reservation.query.get(rid)
        if not r:
            return jsonify({'error': 'reservation_not_found'}), 404

        if r.status == ReservationStatusEnum.fulfilled:
            return jsonify({'error': 'already_checked_in'}), 409
        if r.status in (ReservationStatusEnum.cancelled, ReservationStatusEnum.expired):
            return jsonify({'error': 'reservation_cancelled'}), 409

        data = request.get_json(silent=True) or {}
        license_plate = data.get('licensePlate')
        if not license_plate:
            return jsonify({'error': 'missing_required_field'}), 400

        # Verify plate matches reservation's vehicle
        vehicle = Vehicle.query.get(r.vehicle_id) if r.vehicle_id else None
        if not vehicle or vehicle.license_plate != license_plate:
            return jsonify({'error': 'plate_mismatch'}), 409

        # Assign a spot using reservation's driver class
        effective_class = r.driver_class or 'standard'
        spot, floor = assign_spot(effective_class)
        if not spot:
            return jsonify({'error': 'garage_full'}), 503

        # Occupy the spot
        spot.status = SpotStatusEnum.occupied
        floor.available_spots -= 1

        # Create ticket
        ticket = Ticket(
            spot_id=spot.spot_id,
            vehicle_id=vehicle.vehicle_id,
            entry_timestamp=datetime.utcnow(),
            status=TicketStatusEnum.active,
        )
        db.session.add(ticket)

        # Occupancy log
        db.session.add(OccupancyLog(
            spot_id=spot.spot_id,
            changed_at=datetime.utcnow(),
            change_type=OccupancyChangeEnum.occupied,
        ))

        r.status = ReservationStatusEnum.fulfilled

        db.session.commit()

        # Return ticket JSON
        return jsonify({
            'ticketId':      ticket.ticket_id,
            'licensePlate':  vehicle.license_plate,
            'assignedFloor': floor.floor_number,
            'spotId':        spot.spot_id,
            'entryTime':     ticket.entry_timestamp.isoformat() + 'Z',
            'status':        ticket.status.value,
        }), 201
    except Exception as exc:
        db.session.rollback()
        log_error('reservations.check_in', str(exc))
        return jsonify({'error': 'server_error'}), 500
