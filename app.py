from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from datetime import datetime
import os

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')

db = SQLAlchemy(app)

from models import (
    Floor, ParkingSpot, Vehicle, Ticket, OccupancyLog,
    SpotStatusEnum, SpotTypeEnum, VehicleTypeEnum, TicketStatusEnum, OccupancyChangeEnum
)

with app.app_context():
    db.create_all()


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
    spot_type_val = _DRIVER_CLASS_TO_SPOT_TYPE[driver_class]
    spot = ParkingSpot.query.filter_by(spot_type=spot_type_val, status=SpotStatusEnum.available).first()
    if not spot:
        return jsonify({'error': 'garage_full', 'message': 'No available spots for this driver class'}), 503

    # Step 6 — Create Ticket
    ticket = Ticket(
        vehicle_id=vehicle.vehicle_id,
        spot_id=spot.spot_id,
        entry_timestamp=datetime.utcnow(),
        status=TicketStatusEnum.active,
    )
    db.session.add(ticket)
    db.session.flush()

    # Step 7 — Update spot + floor
    spot.status = SpotStatusEnum.occupied
    floor = Floor.query.get(spot.floor_id)
    floor.available_spots -= 1

    # Step 8 — OccupancyLog
    db.session.add(OccupancyLog(
        spot_id=spot.spot_id,
        changed_at=datetime.utcnow(),
        change_type=OccupancyChangeEnum.occupied,
    ))

    # Step 9 — Commit + return 201
    db.session.commit()

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


if __name__ == '__main__':
    app.run()
