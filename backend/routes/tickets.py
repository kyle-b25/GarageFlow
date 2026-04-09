"""
routes/tickets.py — GarageFlow Ticket Blueprint

All ticket CRUD endpoints:
  POST   /v1/tickets              — Vehicle entry: assign spot, create ticket
  GET    /v1/tickets              — List active tickets (filter by ?plate=, ?phone=, ?status=)
  GET    /v1/tickets/{id}         — Single ticket lookup
  PUT    /v1/tickets/{id}/exit    — Vehicle exit: calculate fee, release spot, create payment
  DELETE /v1/tickets/{id}/personal — Wipe PII (phone, license plate)
  DELETE /v1/tickets/{id}          — Full ticket removal (admin only)
"""

from datetime import datetime

from flask import Blueprint, request, jsonify

from utils import (
    log_error, calculate_duration, calculate_fee,
    release_spot, assign_spot, VALID_DRIVER_CLASSES,
    login_required, require_role,
)

tickets_bp = Blueprint('tickets', __name__, url_prefix='/v1/tickets')


# ------------------------------------------------------------------
#  Constants
# ------------------------------------------------------------------

_VALID_PAYMENT_METHODS = {'cash', 'card', 'mobile'}


# ------------------------------------------------------------------
#  Serializer
# ------------------------------------------------------------------

def _ticket_json(ticket):
    from models import ParkingSpot, Floor, Vehicle

    spot = ParkingSpot.query.get(ticket.spot_id)
    floor = Floor.query.get(spot.floor_id) if spot else None
    vehicle = Vehicle.query.get(ticket.vehicle_id)
    if not spot or not floor or not vehicle:
        return {
            'ticketId': ticket.ticket_id,
            'error': 'incomplete_record',
            'status': ticket.status.value,
        }
    return {
        'ticketId':      ticket.ticket_id,
        'licensePlate':  vehicle.license_plate,
        'phone':         ticket.phone,
        'assignedFloor': floor.floor_number,
        'spotId':        spot.spot_id,
        'entryTime':     ticket.entry_timestamp.isoformat() + 'Z',
        'exitTime':      (ticket.exit_timestamp.isoformat() + 'Z') if ticket.exit_timestamp else None,
        'duration':      ticket.duration,
        'totalFee':      float(ticket.total_fee) if ticket.total_fee is not None else None,
        'status':        ticket.status.value,
    }


# ------------------------------------------------------------------
#  POST /v1/tickets
# ------------------------------------------------------------------

@tickets_bp.route('', methods=['POST'])
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

        # Assign spot (prefers lowest floor number)
        spot, floor = assign_spot(driver_class)
        if not spot:
            return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

        # Create Ticket + mark spot occupied + OccupancyLog
        ticket = Ticket(
            vehicle_id=vehicle.vehicle_id,
            spot_id=spot.spot_id,
            entry_timestamp=datetime.utcnow(),
            status=TicketStatusEnum.active,
            phone=phone,
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
        log_error('tickets.post_ticket', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create ticket'}), 500

    return jsonify({
        'ticketId':      ticket.ticket_id,
        'licensePlate':  license_plate,
        'phone':         ticket.phone,
        'assignedFloor': floor.floor_number,
        'entryTime':     ticket.entry_timestamp.isoformat() + 'Z',
        'status':        'active',
    }), 201


# ------------------------------------------------------------------
#  GET /v1/tickets
# ------------------------------------------------------------------

@tickets_bp.route('', methods=['GET'])
def get_tickets():
    from app import db
    from models import Ticket, Vehicle, TicketStatusEnum

    status_param = request.args.get('status')
    plate_param  = request.args.get('plate') or request.args.get('licensePlate')
    phone_param  = request.args.get('phone')

    q = Ticket.query

    # Default to active tickets; allow explicit status override
    if status_param:
        try:
            q = q.filter_by(status=TicketStatusEnum[status_param])
        except KeyError:
            valid = [s.value for s in TicketStatusEnum]
            return jsonify({'error': 'invalid_status', 'message': f'status must be one of: {", ".join(valid)}'}), 400
    else:
        q = q.filter_by(status=TicketStatusEnum.active)

    try:
        if plate_param:
            vehicle = Vehicle.query.filter(
                Vehicle.license_plate.ilike(plate_param)
            ).first()
            if not vehicle:
                return jsonify([]), 200
            q = q.filter_by(vehicle_id=vehicle.vehicle_id)

        if phone_param:
            q = q.filter_by(phone=phone_param)

        tickets = q.order_by(Ticket.entry_timestamp.desc()).all()
        return jsonify([_ticket_json(t) for t in tickets]), 200
    except Exception as exc:
        db.session.rollback()
        log_error('tickets.get_tickets', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch tickets'}), 500


# ------------------------------------------------------------------
#  GET /v1/tickets/<id>
# ------------------------------------------------------------------

@tickets_bp.route('/<int:ticket_id>', methods=['GET'])
def get_ticket(ticket_id):
    from app import db
    from models import Ticket

    try:
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({'error': 'ticket_not_found', 'message': 'No ticket found with that ID'}), 404
        return jsonify(_ticket_json(ticket)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('tickets.get_ticket', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch ticket'}), 500


# ------------------------------------------------------------------
#  PUT /v1/tickets/<id>/exit
# ------------------------------------------------------------------

@tickets_bp.route('/<int:ticket_id>/exit', methods=['PUT'])
def put_ticket_exit(ticket_id):
    from app import db
    from models import (
        Ticket, Vehicle, TicketStatusEnum,
        Payment, PaymentMethodEnum, PaymentStatusEnum,
    )

    _CLOSED_STATUSES = {TicketStatusEnum.closed, TicketStatusEnum.voided, TicketStatusEnum.lost}

    # Step 1 — Look up ticket
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'ticket_not_found', 'message': 'No ticket found with that ID'}), 404

    # Step 2 — Validate status
    if ticket.status in _CLOSED_STATUSES:
        return jsonify({'error': 'ticket_already_closed', 'message': 'Ticket is not active'}), 409

    # Step 3 — Parse + validate request body
    data = request.get_json(silent=True) or {}
    license_plate  = data.get('licensePlate')
    payment_method = data.get('paymentMethod')

    if not license_plate:
        return jsonify({'error': 'missing_required_field', 'message': 'licensePlate is required'}), 400
    if not payment_method:
        return jsonify({'error': 'missing_required_field', 'message': 'paymentMethod is required'}), 400
    if payment_method not in _VALID_PAYMENT_METHODS:
        return jsonify({'error': 'invalid_payment_method', 'message': f'paymentMethod must be one of: {", ".join(sorted(_VALID_PAYMENT_METHODS))}'}), 400

    # Step 4 — Plate confirmation
    vehicle = Vehicle.query.get(ticket.vehicle_id)
    if vehicle.license_plate.lower() != license_plate.lower():
        return jsonify({'error': 'plate_mismatch', 'message': 'License plate does not match this ticket'}), 409

    # Step 5 — Stamp exit + calculate duration/fee
    exit_ts = datetime.utcnow()
    ticket.exit_timestamp = exit_ts
    ticket.duration = calculate_duration(ticket.entry_timestamp, exit_ts)
    ticket.total_fee = calculate_fee(ticket.duration)
    ticket.status = TicketStatusEnum.closed

    # Step 6 — Create Payment
    db.session.add(Payment(
        ticket_id=ticket.ticket_id,
        amount_charged=ticket.total_fee,
        payment_method=PaymentMethodEnum[payment_method],
        payment_status=PaymentStatusEnum.pending,
        payment_timestamp=exit_ts,
    ))

    # Step 7 — Free spot + update floor counter + OccupancyLog
    if not release_spot(ticket.spot_id, exit_ts):
        db.session.rollback()
        return jsonify({'error': 'server_error', 'message': 'Could not release spot'}), 500

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('tickets.put_ticket_exit', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to process exit'}), 500

    return jsonify({
        'ticketId':      ticket.ticket_id,
        'licensePlate':  vehicle.license_plate,
        'exitTime':      exit_ts.isoformat() + 'Z',
        'duration':      ticket.duration,
        'totalFee':      float(ticket.total_fee),
        'paymentStatus': 'pending',
        'status':        'closed',
    }), 200


# ------------------------------------------------------------------
#  DELETE /v1/tickets/<id>/personal
# ------------------------------------------------------------------

@tickets_bp.route('/<int:ticket_id>/personal', methods=['DELETE'])
@login_required
def delete_ticket_personal(ticket_id):
    from app import db
    from models import Ticket, Vehicle

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'ticket_not_found', 'message': 'No ticket found with that ID'}), 404

    # Wipe phone from ticket
    ticket.phone = None

    # Only redact the vehicle plate if no other tickets reference this vehicle
    vehicle = Vehicle.query.get(ticket.vehicle_id)
    if vehicle:
        other_tickets = Ticket.query.filter(
            Ticket.vehicle_id == vehicle.vehicle_id,
            Ticket.ticket_id != ticket.ticket_id,
        ).first()
        if other_tickets:
            # Dissociate this ticket from the shared vehicle instead of mutating it
            redacted_vehicle = Vehicle(
                license_plate=f'REDACTED-{ticket.ticket_id}',
                vehicle_type=vehicle.vehicle_type,
            )
            db.session.add(redacted_vehicle)
            db.session.flush()
            ticket.vehicle_id = redacted_vehicle.vehicle_id
        else:
            vehicle.license_plate = f'REDACTED-{vehicle.vehicle_id}'

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('tickets.delete_personal', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to wipe personal data'}), 500

    return '', 204


# ------------------------------------------------------------------
#  DELETE /v1/tickets/<id>
# ------------------------------------------------------------------

@tickets_bp.route('/<int:ticket_id>', methods=['DELETE'])
@require_role('admin')
def delete_ticket(ticket_id):
    from app import db
    from models import Ticket, Payment, TicketStatusEnum

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'ticket_not_found', 'message': 'No ticket found with that ID'}), 404

    # Release spot if ticket is active
    if ticket.status == TicketStatusEnum.active:
        release_spot(ticket.spot_id, datetime.utcnow())

    # Delete associated payment first (FK constraint)
    if ticket.payment:
        db.session.delete(ticket.payment)

    db.session.delete(ticket)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('tickets.delete_ticket', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to delete ticket'}), 500

    return '', 204
