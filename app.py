from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from datetime import datetime, timedelta
from decimal import Decimal
import math
import re
import os

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)

db = SQLAlchemy(app)

from models import (
    Floor, ParkingSpot, Vehicle, Ticket, OccupancyLog,
    SpotStatusEnum, SpotTypeEnum, VehicleTypeEnum, TicketStatusEnum, OccupancyChangeEnum,
    Reservation, ReservationStatusEnum,
    Payment, PaymentMethodEnum, PaymentStatusEnum,
)

with app.app_context():
    db.create_all()


@app.route('/operator-front')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return {'status': 'ok'}


_DRIVER_CLASS_TO_SPOT_TYPE = {
    'standard':      SpotTypeEnum.standard,
    'accessibility': SpotTypeEnum.accessibility,
    'employee':      SpotTypeEnum.staff,
    'eco':           SpotTypeEnum.standard,
}

VALID_DRIVER_CLASSES = set(_DRIVER_CLASS_TO_SPOT_TYPE.keys())


def assign_spot(driver_class):
    """
    Find the best available (spot, floor) pair for the given driver class.

    Floors are sorted by availability ratio (available/total) descending,
    tiebreak by floor_number ascending. Sorting done in Python — SQLite
    integer division truncates and cannot be used in ORDER BY for ratios.

    Returns (spot, floor) on success, or (None, None) if garage is full.
    """
    spot_type_val = _DRIVER_CLASS_TO_SPOT_TYPE[driver_class]

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


def release_spot(spot_id, exit_ts):
    """
    Free a parking spot: set status to available, increment floor counter,
    append OccupancyLog. Does NOT commit — caller owns the transaction.

    Returns the ParkingSpot on success, or None if spot_id not found.
    """
    spot = ParkingSpot.query.get(spot_id)
    if not spot:
        return None

    spot.status = SpotStatusEnum.available

    floor = Floor.query.get(spot.floor_id)
    if floor:
        floor.available_spots += 1

    db.session.add(OccupancyLog(
        spot_id=spot.spot_id,
        changed_at=exit_ts,
        change_type=OccupancyChangeEnum.freed,
    ))
    return spot


@app.route('/v1/tickets', methods=['POST'])
def post_ticket():
    data = request.get_json(silent=True) or {}

    license_plate = data.get('licensePlate')
    driver_class  = data.get('driverClass')
    phone         = data.get('phone')

    # Step 1 — Validate input
    if not license_plate or not driver_class:
        missing = 'licensePlate' if not license_plate else 'driverClass'
        return jsonify({'error': 'missing_required_field', 'message': f'{missing} is required'}), 400

    if driver_class not in VALID_DRIVER_CLASSES:
        return jsonify({
            'error': 'missing_required_field',
            'message': f'driverClass must be one of: {", ".join(sorted(VALID_DRIVER_CLASSES))}',
        }), 400

    # Step 2 — Check global availability
    if not Floor.query.filter(Floor.available_spots > 0).first():
        return jsonify({'error': 'garage_full', 'message': 'Garage is full'}), 503

    # Step 3 — Get or create Vehicle
    vehicle = Vehicle.query.filter_by(license_plate=license_plate).first()
    if not vehicle:
        vehicle = Vehicle(
            license_plate=license_plate,
            vehicle_type=VehicleTypeEnum.car,
            customer_id=None,
        )
        db.session.add(vehicle)
        db.session.flush()

    # Step 4 — Duplicate active ticket check
    if Ticket.query.filter_by(vehicle_id=vehicle.vehicle_id, status=TicketStatusEnum.active).first():
        return jsonify({'error': 'duplicate_plate', 'message': 'Vehicle already has an active ticket'}), 409

    # Step 5 — Assign spot
    spot, floor = assign_spot(driver_class)
    if not spot:
        return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

    # Step 6 — Create Ticket
    ticket = Ticket(
        vehicle_id=vehicle.vehicle_id,
        spot_id=spot.spot_id,
        entry_timestamp=datetime.utcnow(),
        status=TicketStatusEnum.active,
    )

    # Steps 7–9 — Write to DB
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
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error', 'message': 'Failed to create ticket'}), 500

    response = {
        'ticketId':     ticket.ticket_id,
        'licensePlate': license_plate,
        'assignedFloor': floor.floor_number,
        'entryTime':    ticket.entry_timestamp.isoformat() + 'Z',
        'status':       'active',
    }
    if phone:
        response['phone'] = phone

    return jsonify(response), 201


def _ticket_json(ticket):
    spot = ParkingSpot.query.get(ticket.spot_id)
    floor = Floor.query.get(spot.floor_id)
    vehicle = Vehicle.query.get(ticket.vehicle_id)
    return {
        'ticketId':     ticket.ticket_id,
        'licensePlate': vehicle.license_plate,
        'assignedFloor': floor.floor_number,
        'spotId':       spot.spot_id,
        'entryTime':    ticket.entry_timestamp.isoformat() + 'Z',
        'exitTime':     (ticket.exit_timestamp.isoformat() + 'Z') if ticket.exit_timestamp else None,
        'duration':     ticket.duration,
        'totalFee':     float(ticket.total_fee) if ticket.total_fee is not None else None,
        'status':       ticket.status.value,
    }


@app.route('/v1/tickets', methods=['GET'])
def get_tickets():
    status_param = request.args.get('status')
    plate_param  = request.args.get('licensePlate')

    q = Ticket.query
    if status_param:
        try:
            q = q.filter_by(status=TicketStatusEnum[status_param])
        except KeyError:
            valid = [s.value for s in TicketStatusEnum]
            return jsonify({'error': 'invalid_status', 'message': f'status must be one of: {", ".join(valid)}'}), 400
    if plate_param:
        vehicle = Vehicle.query.filter(
            Vehicle.license_plate.ilike(plate_param)
        ).first()
        if not vehicle:
            return jsonify([]), 200
        q = q.filter_by(vehicle_id=vehicle.vehicle_id)

    tickets = q.order_by(Ticket.entry_timestamp.desc()).all()
    return jsonify([_ticket_json(t) for t in tickets]), 200


@app.route('/v1/tickets/<int:ticket_id>', methods=['GET'])
def get_ticket(ticket_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'ticket_not_found', 'message': 'No ticket found with that ID'}), 404
    return jsonify(_ticket_json(ticket)), 200


_STATUS_MAP = {
    ReservationStatusEnum.confirmed: 'confirmed',
    ReservationStatusEnum.fulfilled: 'complete',
    ReservationStatusEnum.expired:   'expired',
    ReservationStatusEnum.cancelled: 'cancelled',
}


@app.route('/v1/reservations', methods=['POST'])
def post_reservation():
    data = request.get_json(silent=True) or {}

    phone            = data.get('phone')
    scheduled_arrival = data.get('scheduledArrival')
    driver_class     = data.get('driverClass')

    # Step 1 — Validate input
    if not phone or not scheduled_arrival:
        missing = 'phone' if not phone else 'scheduledArrival'
        return jsonify({'error': 'missing_required_field', 'message': f'{missing} is required'}), 400

    try:
        parsed_arrival = datetime.fromisoformat(scheduled_arrival.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return jsonify({'error': 'invalid_scheduled_arrival', 'message': 'scheduledArrival is not a valid ISO 8601 datetime'}), 400

    from datetime import timezone
    now = datetime.now(timezone.utc)
    if parsed_arrival <= now:
        return jsonify({'error': 'invalid_scheduled_arrival', 'message': 'scheduledArrival must be in the future'}), 400

    # Step 2 — Find advisory floor
    effective_class = driver_class if driver_class in _DRIVER_CLASS_TO_SPOT_TYPE else 'standard'
    spot, floor = assign_spot(effective_class)
    if not spot:
        return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

    # Step 3 — Create Reservation (do NOT mark spot occupied)
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
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'server_error', 'message': 'Failed to create reservation'}), 500

    return jsonify({
        'reservationId':    f'R-{reservation.reservation_id:04d}',
        'assignedFloor':    floor.floor_number,
        'scheduledArrival': scheduled_arrival,
        'status':           'confirmed',
    }), 201


@app.route('/v1/reservations', methods=['GET'])
def get_reservations():
    phone = request.args.get('phone')
    if not phone:
        return jsonify({'error': 'missing_required_field', 'message': 'phone is required'}), 400

    include_old = request.args.get('includeOld', 'false').lower() == 'true'

    q = Reservation.query.filter_by(phone=phone)
    if not include_old:
        q = q.filter_by(status=ReservationStatusEnum.confirmed)
    reservations = q.order_by(Reservation.start_datetime).all()

    result = [
        {
            'reservationId':    f'R-{r.reservation_id:04d}',
            'assignedFloor':    r.floor_number if r.floor_number is not None else -1,
            'scheduledArrival': r.start_datetime.isoformat() + 'Z',
            'status':           _STATUS_MAP[r.status],
        }
        for r in reservations
    ]
    return jsonify(result), 200


_VALID_PAYMENT_METHODS = {'cash', 'card', 'mobile'}
_CLOSED_STATUSES = {TicketStatusEnum.closed, TicketStatusEnum.voided, TicketStatusEnum.lost}


@app.route('/v1/tickets/<int:ticket_id>/exit', methods=['PUT'])
def put_ticket_exit(ticket_id):
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
    duration_minutes = math.ceil((exit_ts - ticket.entry_timestamp).total_seconds() / 60)
    ticket.duration = duration_minutes
    ticket.total_fee = Decimal('5.00') + Decimal('2.00') * math.ceil(duration_minutes / 60)
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

    db.session.commit()

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
#  Blueprints — imported after models to avoid circular imports
# ------------------------------------------------------------------

from auth import auth_bp
from staff_routes import staff_bp

app.register_blueprint(auth_bp)
app.register_blueprint(staff_bp)


# ------------------------------------------------------------------
#  CLI commands
# ------------------------------------------------------------------

@app.cli.command('seed-admin')
def seed_admin():
    """Create a default admin account (username: admin, password: admin)."""
    import bcrypt
    from models import Staff, StaffRoleEnum

    if Staff.query.filter_by(username='admin').first():
        print('Admin account already exists — skipping.')
        return

    password_hash = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode('utf-8')
    db.session.add(Staff(
        name='System Admin',
        username='admin',
        password_hash=password_hash,
        role=StaffRoleEnum.admin,
    ))
    db.session.commit()
    print('Admin account created (username: admin, password: admin).')


if __name__ == '__main__':
    app.run()
