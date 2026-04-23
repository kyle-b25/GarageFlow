"""
routes/_routes.py — GarageFlow Public API Blueprint

Endpoints the operator kiosk frontend depends on:
  GET  /v1/capacity
  GET  /v1/garage
  POST /v1/webhooks/stripe

Ticket endpoints live in routes/tickets.py.
Reservation endpoints live in reservations_bp.py.
No authentication required.  Registered in app.py as v1_bp.
"""

import os
from datetime import datetime

import stripe
from flask import Blueprint, request, jsonify

from utils import log_error, calculate_duration, calculate_fee, require_role

v1_bp = Blueprint('v1', __name__, url_prefix='/v1')


# ------------------------------------------------------------------
#  Shared constants
# ------------------------------------------------------------------

STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')



# ------------------------------------------------------------------
#  Helpers
# ------------------------------------------------------------------

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
#  GET /v1/capacity
# ------------------------------------------------------------------

@v1_bp.route('/capacity', methods=['GET'])
def get_capacity():
    """Return total, occupied, and available spot counts by type."""
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
    """Return available spot counts by type."""
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

@v1_bp.route('/capacity/floors/<int:floor_id>', methods=['GET'])
def get_capacity_floor(floor_id):
    """Return capacity breakdown for a single floor."""
    from app import db
    from models import Floor, ParkingSpot

    try:
        floor = Floor.query.filter_by(floor_id=floor_id).first()
        if not floor:
            return jsonify({'error': 'floor_not_found', 'message': 'No floor found with that ID'}), 404

        spots = ParkingSpot.query.filter_by(floor_id=floor_id).all()
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
        log_error('routes.get_garage', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch garage info'}), 500
        


# ------------------------------------------------------------------
#  GET /v1/capacity/alert — Congestion alert
# ------------------------------------------------------------------

_CONGESTION_THRESHOLD = float(os.getenv('CONGESTION_THRESHOLD', '0.85'))

@v1_bp.route('/capacity/alert', methods=['GET'])
def capacity_alert():
    """Return congestion alert state based on configurable threshold."""
    from app import db
    from models import ParkingSpot

    try:
        spots = ParkingSpot.query.all()
        total, occupied, available, _ = _count_spots(spots)
        rate = occupied / total if total > 0 else 0
        alert = rate >= _CONGESTION_THRESHOLD

        return jsonify({
            'alert': alert,
            'occupancyRate': round(rate, 4),
            'threshold': _CONGESTION_THRESHOLD,
            'total': total,
            'occupied': occupied,
            'available': available,
        }), 200
    except Exception as exc:
        db.session.rollback()
        log_error('routes.capacity_alert', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to check congestion'}), 500


# ------------------------------------------------------------------
#  POST /v1/garage — Admin garage configuration (SR-13)
# ------------------------------------------------------------------

@v1_bp.route('/garage', methods=['POST'])
@require_role('admin')
def create_garage():
    """Create a new garage. Replaces the interactive garage_builder workflow."""
    from app import db
    from models import Garage

    data = request.get_json(silent=True) or {}
    name = data.get('name')
    total_capacity = data.get('totalCapacity')
    number_of_floors = data.get('numberOfFloors')
    operating_hours = data.get('operatingHours')

    if not name or not total_capacity or not number_of_floors or not operating_hours:
        return jsonify({'error': 'missing_required_field',
                        'message': 'name, totalCapacity, numberOfFloors, and operatingHours are required'}), 400

    try:
        garage = Garage(
            name=name,
            total_capacity=total_capacity,
            number_of_floors=number_of_floors,
            operating_hours=operating_hours,
            front_desk_phone=data.get('frontDeskPhone'),
        )
        db.session.add(garage)
        db.session.commit()
        return jsonify({
            'garageId': garage.garage_id,
            'name': garage.name,
            'totalCapacity': garage.total_capacity,
            'numberOfFloors': garage.number_of_floors,
            'operatingHours': garage.operating_hours,
            'frontDeskPhone': garage.front_desk_phone,
        }), 201
    except Exception as exc:
        db.session.rollback()
        log_error('routes.create_garage', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create garage'}), 500


# ------------------------------------------------------------------
#  PUT /v1/garage/<id> — Update garage configuration
# ------------------------------------------------------------------

@v1_bp.route('/garage/<int:garage_id>', methods=['PUT'])
@require_role('admin')
def update_garage(garage_id):
    """Update an existing garage's configuration."""
    from app import db
    from models import Garage

    try:
        garage = Garage.query.get(garage_id)
        if not garage:
            return jsonify({'error': 'garage_not_found', 'message': 'No garage found with that ID'}), 404

        data = request.get_json(silent=True) or {}
        if 'name' in data:
            garage.name = data['name']
        if 'totalCapacity' in data:
            garage.total_capacity = data['totalCapacity']
        if 'numberOfFloors' in data:
            garage.number_of_floors = data['numberOfFloors']
        if 'operatingHours' in data:
            garage.operating_hours = data['operatingHours']
        if 'frontDeskPhone' in data:
            garage.front_desk_phone = data['frontDeskPhone']

        db.session.commit()
        return jsonify({
            'garageId': garage.garage_id,
            'name': garage.name,
            'totalCapacity': garage.total_capacity,
            'numberOfFloors': garage.number_of_floors,
            'operatingHours': garage.operating_hours,
            'frontDeskPhone': garage.front_desk_phone,
        }), 200
    except Exception as exc:
        db.session.rollback()
        log_error('routes.update_garage', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to update garage'}), 500


# ------------------------------------------------------------------
#  DELETE /v1/garage/<id> — Delete garage configuration
# ------------------------------------------------------------------

@v1_bp.route('/garage/<int:garage_id>', methods=['DELETE'])
@require_role('admin')
def delete_garage(garage_id):
    """Delete a garage. Cascades to floors, spots, gates."""
    from app import db
    from models import Garage

    try:
        garage = Garage.query.get(garage_id)
        if not garage:
            return jsonify({'error': 'garage_not_found', 'message': 'No garage found with that ID'}), 404

        db.session.delete(garage)
        db.session.commit()
        return jsonify({'message': 'Garage deleted'}), 200
    except Exception as exc:
        db.session.rollback()
        log_error('routes.delete_garage', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to delete garage'}), 500


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
    # Do not commit here — let the handler's transaction include the dedup record
    return False


def _find_payment_by_intent(intent_id):
    """Look up a Payment by its Stripe PaymentIntent ID."""
    from models import Payment
    return Payment.query.filter_by(stripe_payment_intent_id=intent_id).first()


def _handle_payment_succeeded(intent_data):
    from app import db
    from models import (
        PaymentStatusEnum, TicketStatusEnum,
        ParkingSpot, Floor, OccupancyLog, SpotStatusEnum, OccupancyChangeEnum,
    )

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

            # Release the parking spot
            spot = ParkingSpot.query.get(ticket.spot_id)
            if spot and spot.status != SpotStatusEnum.available:
                spot.status = SpotStatusEnum.available
                floor = Floor.query.get(spot.floor_id)
                if floor:
                    floor.available_spots += 1
                db.session.add(OccupancyLog(
                    spot_id=spot.spot_id,
                    changed_at=ticket.exit_timestamp,
                    change_type=OccupancyChangeEnum.freed,
                ))

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
    """Handle incoming Stripe webhook events."""
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
