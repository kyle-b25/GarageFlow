from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from datetime import datetime, timedelta
from decimal import Decimal
import math
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



def _log_error(source, description):
    """Append a row to SystemEvent for auditing failures."""
    try:
        from models import SystemEvent
        db.session.add(SystemEvent(source=source, description=description))
        db.session.commit()
    except Exception:
        pass


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

    try:
        if plate_param:
            vehicle = Vehicle.query.filter(
                Vehicle.license_plate.ilike(plate_param)
            ).first()
            if not vehicle:
                return jsonify([]), 200
            q = q.filter_by(vehicle_id=vehicle.vehicle_id)

        tickets = q.order_by(Ticket.entry_timestamp.desc()).all()
        return jsonify([_ticket_json(t) for t in tickets]), 200
    except Exception as exc:
        db.session.rollback()
        _log_error('app.get_tickets', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch tickets'}), 500


@app.route('/v1/tickets/<int:ticket_id>', methods=['GET'])
def get_ticket(ticket_id):
    try:
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({'error': 'ticket_not_found', 'message': 'No ticket found with that ID'}), 404
        return jsonify(_ticket_json(ticket)), 200
    except Exception as exc:
        db.session.rollback()
        _log_error('app.get_ticket', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to fetch ticket'}), 500



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

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_error('app.put_ticket_exit', str(exc))
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
#  Blueprints — imported after models to avoid circular imports
# ------------------------------------------------------------------

from routes import v1_bp
from auth import auth_bp
from staff_routes import staff_bp
from routes.payments import payments_bp

app.register_blueprint(v1_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(staff_bp)
app.register_blueprint(payments_bp)


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
