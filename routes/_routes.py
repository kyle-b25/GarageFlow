"""
routes.py — GarageFlow Public API Blueprint

The four endpoints the operator kiosk frontend depends on:
  POST /v1/tickets
  POST /v1/reservations
  GET  /v1/reservations
  GET  /v1/floors

No authentication required.  Registered in app.py as v1_bp.
"""

import os
import re
from datetime import datetime

import stripe
from flask import Blueprint, request, jsonify

from utils import log_error, calculate_duration, calculate_fee

v1_bp = Blueprint('v1', __name__, url_prefix='/v1')


# ------------------------------------------------------------------
#  Shared constants
# ------------------------------------------------------------------

_DRIVER_CLASS_TO_SPOT_TYPE = {
    'standard':      'standard',
    'accessibility': 'accessibility',
    'employee':      'staff',
    'eco':           'staff',
}

VALID_DRIVER_CLASSES = set(_DRIVER_CLASS_TO_SPOT_TYPE.keys())

STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

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


def _count_spots(spots):
    """Tally spot counts by type. Returns (total, occupied, available, by_type dict)."""
    from models import SpotTypeEnum, SpotStatusEnum

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

    return total, occupied, available, by_type


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
            'error': 'invalid_driver_class',
            'message': f'driverClass must be one of: {", ".join(sorted(VALID_DRIVER_CLASSES))}',
        }), 400

    # Check global availability
    if not Floor.query.filter(Floor.available_spots > 0).first():
        return jsonify({'error': 'garage_full', 'message': 'Garage is full'}), 503

    try:
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
        log_error('routes.post_ticket', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create ticket'}), 500

    return jsonify({
        'ticketId':      ticket.ticket_id,
        'licensePlate':  license_plate,
        'assignedFloor': floor.floor_number,
        'entryTime':     ticket.entry_timestamp.isoformat() + 'Z',
        'status':        'active',
    }), 201


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

    now = datetime.utcnow()
    if parsed_arrival.replace(tzinfo=None) <= now:
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
        log_error('routes.post_reservation', str(exc))
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
    from app import db
    from models import Reservation, ReservationStatusEnum

    phone = request.args.get('phone')
    if not phone:
        return jsonify({'error': 'missing_required_field', 'message': 'phone is required'}), 400

    try:
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
    except Exception as exc:
        db.session.rollback()
        log_error('routes.get_reservations', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch reservations'}), 500


# ------------------------------------------------------------------
#  GET /v1/reservations/upcoming?limit=10
# ------------------------------------------------------------------

@v1_bp.route('/reservations/upcoming', methods=['GET'])
def get_upcoming_reservations():
    from app import db
    from models import Reservation, ReservationStatusEnum

    try:
        limit = request.args.get('limit', 10)
        try:
            limit = int(limit)
            if limit < 1 or limit > 100:
                return jsonify({'error': 'invalid_limit', 'message': 'limit must be between 1 and 100'}), 400
        except ValueError:
            return jsonify({'error': 'invalid_limit', 'message': 'limit must be a whole number'}), 400

        now = datetime.utcnow()


        reservations = (
            Reservation.query
            .filter(
                Reservation.status == ReservationStatusEnum.confirmed,
                Reservation.start_datetime >= now,
            )
            .order_by(Reservation.start_datetime.asc())
            .limit(limit)
            .all()
        )

        status_map = _get_status_map()
        result = [
            {
                'reservationId':    f'R-{r.reservation_id:04d}',
                'phone':            r.phone,
                'assignedFloor':    r.floor_number if r.floor_number is not None else -1,
                'scheduledArrival': r.start_datetime.isoformat() + 'Z',
                'status':           status_map[r.status],
            }
            for r in reservations
        ]
        return jsonify(result), 200

    except Exception as exc:
        db.session.rollback()
        _log_error('routes.get_upcoming_reservations', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch upcoming reservations'}), 500

# ------------------------------------------------------------------
#  GET /v1/floors
# ------------------------------------------------------------------

@v1_bp.route('/floors', methods=['GET'])
def get_floors():
    from app import db
    from models import Floor, SpotStatusEnum

    try:
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
                'available': floor.available_spots,
                'zones': zones,
            })

        return jsonify(result), 200
    except Exception as exc:
        db.session.rollback()
        log_error('routes.get_floors', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch floors'}), 500


# ------------------------------------------------------------------
#  GET /v1/capacity
# ------------------------------------------------------------------

@v1_bp.route('/capacity', methods=['GET'])
def get_capacity():
    from app import db
    from models import ParkingSpot

    try:
        spots = ParkingSpot.query.all()
        total, occupied, available, by_type = _count_spots(spots)

        return jsonify({
            'total': total,
            'occupied': occupied,
            'available': available,
            'byType': by_type,
        }), 200
    except Exception as exc:
        db.session.rollback()
        log_error('routes.get_capacity', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch capacity'}), 500


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
        log_error('routes.get_capacity_status', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch capacity status'}), 500


# ------------------------------------------------------------------
#  GET /v1/capacity/floors/<floorId>
# ------------------------------------------------------------------

@v1_bp.route('/capacity/floors/<int:floorId>', methods=['GET'])
def get_capacity_floor(floorId):
    from app import db
    from models import Floor, ParkingSpot

    try:
        floor = Floor.query.filter_by(floor_id=floorId).first()
        if not floor:
            return jsonify({'error': 'floor_not_found'}), 404

        spots = ParkingSpot.query.filter_by(floor_id=floorId).all()
        total, occupied, available, by_type = _count_spots(spots)

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
        log_error('routes.get_capacity_floor', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch floor capacity'}), 500



# ------------------------------------------------------------------
#  GET /v1/garage
#  Sam Gibney 3/27/2026
# ------------------------------------------------------------------

@v1_bp.route('/garage', methods=['GET'])
def get_garage():
    from app import db
    from models import Garage

    try:
        garage = Garage.query.first()
        if not garage:
            return jsonify({'error': 'garage_not_found', 'message': 'No garage configured'}), 404
        return jsonify({
            'garageId':        garage.garage_id,
            'name':            garage.name,
            'totalCapacity':   garage.total_capacity,
            'numberOfFloors':  garage.number_of_floors,
            'operatingHours':  garage.operating_hours,
            'frontDeskPhone':  garage.front_desk_phone,
        }), 200
    except Exception as exc:
        db.session.rollback()
        _log_error('routes.get_garage', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch garage info'}), 500
        
        
# ------------------------------------------------------------------
#  Stripe webhook helpers
# ------------------------------------------------------------------

def _is_duplicate_event(event_id):
    """Check if a Stripe event has already been processed (exact match)."""
    from app import db
    from models import SystemEvent

    existing = SystemEvent.query.filter_by(
        source='stripe_webhook',
        description=event_id,
    ).first()
    if existing:
        return True

    db.session.add(SystemEvent(
        source='stripe_webhook',
        description=event_id,
    ))
    db.session.commit()
    return False


def _find_payment_by_intent(intent_id):
    """Look up a Payment by its Stripe PaymentIntent ID."""
    from models import Payment
    return Payment.query.filter_by(stripe_payment_intent_id=intent_id).first()


def _handle_payment_succeeded(intent_data):
    from app import db
    from models import PaymentStatusEnum, TicketStatusEnum

    try:
        intent_id = intent_data['id']
        payment = _find_payment_by_intent(intent_id)
        if not payment:
            log_error('stripe_webhook', f'No payment found for intent {intent_id}')
            return

        payment.payment_status = PaymentStatusEnum.paid
        ticket = payment.ticket

        if ticket.status == TicketStatusEnum.active:
            if not ticket.exit_timestamp:
                ticket.exit_timestamp = datetime.utcnow()
            ticket.duration = calculate_duration(ticket.entry_timestamp, ticket.exit_timestamp)
            ticket.total_fee = calculate_fee(ticket.duration)
            ticket.status = TicketStatusEnum.closed

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('stripe_webhook.payment_succeeded', str(exc))


def _handle_payment_failed(intent_data):
    from app import db
    from models import PaymentStatusEnum, SystemEvent

    try:
        intent_id = intent_data['id']
        payment = _find_payment_by_intent(intent_id)
        if not payment:
            log_error('stripe_webhook', f'No payment found for intent {intent_id}')
            return

        payment.payment_status = PaymentStatusEnum.failed

        error_msg = intent_data.get('last_payment_error', {}).get('message', 'unknown')
        db.session.add(SystemEvent(
            source='stripe_webhook',
            description=f'Payment failed for intent {intent_id}: {error_msg}',
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('stripe_webhook.payment_failed', str(exc))


def _handle_payment_canceled(intent_data):
    from app import db
    from models import PaymentStatusEnum, TicketStatusEnum, SystemEvent

    try:
        intent_id = intent_data['id']
        payment = _find_payment_by_intent(intent_id)
        if not payment:
            log_error('stripe_webhook', f'No payment found for intent {intent_id}')
            return

        payment.payment_status = PaymentStatusEnum.failed
        if not payment.ticket:
            log_error('stripe_webhook', f'Payment {payment.payment_id} has no linked ticket')
            return
        payment.ticket.status = TicketStatusEnum.voided

        db.session.add(SystemEvent(
            source='stripe_webhook',
            description=f'Payment canceled for intent {intent_id}',
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('stripe_webhook.payment_canceled', str(exc))


def _handle_charge_refunded(charge_data):
    from app import db
    from models import PaymentStatusEnum, SystemEvent

    try:
        intent_id = charge_data.get('payment_intent')
        if not intent_id:
            log_error('stripe_webhook', 'charge.refunded event missing payment_intent')
            return

        payment = _find_payment_by_intent(intent_id)
        if not payment:
            log_error('stripe_webhook', f'No payment found for intent {intent_id}')
            return

        payment.payment_status = PaymentStatusEnum.refunded

        db.session.add(SystemEvent(
            source='stripe_webhook',
            description=f'Payment refunded for intent {intent_id}',
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('stripe_webhook.charge_refunded', str(exc))


# ------------------------------------------------------------------
#  POST /v1/webhooks/stripe
# ------------------------------------------------------------------

@v1_bp.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    from app import db

    # Step 1 — Signature verification
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_WEBHOOK_SECRET:
        log_error('stripe_webhook', 'STRIPE_WEBHOOK_SECRET not configured')
        return jsonify({'error': 'webhook_not_configured'}), 500

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({'error': 'invalid_signature'}), 401

    # Step 2 — Deduplication
    if _is_duplicate_event(event['id']):
        return jsonify({'status': 'duplicate'}), 200

    # Step 3 — Dispatch to handler
    event_type = event['type']
    event_data = event['data']['object']

    try:
        if event_type == 'payment_intent.succeeded':
            _handle_payment_succeeded(event_data)
        elif event_type == 'payment_intent.payment_failed':
            _handle_payment_failed(event_data)
        elif event_type == 'payment_intent.canceled':
            _handle_payment_canceled(event_data)
        elif event_type == 'charge.refunded':
            _handle_charge_refunded(event_data)
    except Exception as exc:
        db.session.rollback()
        log_error('stripe_webhook', f'Handler error for {event_type}: {exc}')

    return jsonify({'status': 'ok'}), 200
