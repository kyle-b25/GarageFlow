"""
routes/customer.py — GarageFlow Customer-Facing API Blueprint

Endpoints for customer self-service:
  POST /v1/customer/register     — Create customer account
  POST /v1/customer/login        — Authenticate with email/password
  POST /v1/customer/logout       — Revoke current session
  GET  /v1/customer/me           — Current customer profile
  GET  /v1/customer/reservations — List own reservations
  POST /v1/customer/reservations — Create a reservation (self-service)
  GET  /v1/customer/vehicles     — List own vehicles
  POST /v1/customer/vehicles     — Add a vehicle
"""

import secrets
from datetime import datetime, timedelta

import bcrypt
from flask import Blueprint, request, jsonify, g

from app import db
from models import (
    Customer, Vehicle, Reservation, AccountStatusEnum,
    VehicleTypeEnum, ReservationStatusEnum,
)
from utils import assign_spot, log_error, _DRIVER_CLASS_TO_SPOT_TYPE

customer_bp = Blueprint('customer', __name__, url_prefix='/v1/customer')


# ── Customer session model ───────────────────────────────────────
# Reuse a lightweight approach: store a password hash on Customer
# and use a simple token table. Since Customer doesn't have a
# password_hash field, we add one via a new model.

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship


class CustomerAuth(db.Model):
    """Password and session storage for customer accounts."""
    __tablename__ = 'customer_auth'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.customer_id',
                            ondelete='CASCADE'), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    customer = db.relationship('Customer', backref=db.backref('auth', uselist=False))


class CustomerSession(db.Model):
    """Bearer token sessions for customer portal."""
    __tablename__ = 'customer_session'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.customer_id',
                            ondelete='CASCADE'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    expires_at = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    customer = db.relationship('Customer')


_TOKEN_TTL_HOURS = 24


# ── Auth helpers ─────────────────────────────────────────────────

def _get_current_customer():
    """Validate customer Bearer token, return Customer or None."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token_str = auth_header[7:]
    if not token_str:
        return None

    session = CustomerSession.query.filter_by(
        token=token_str, is_active=True
    ).first()
    if not session or session.expires_at < datetime.utcnow():
        return None

    customer = Customer.query.get(session.customer_id)
    if not customer or customer.account_status != AccountStatusEnum.active:
        return None

    g.current_customer = customer
    g.customer_session = session
    return customer


def customer_login_required(f):
    """Decorator — rejects requests without a valid customer token."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _get_current_customer():
            return jsonify({'error': 'unauthorized', 'message': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated


def _create_customer_token(customer_id):
    now = datetime.utcnow()
    token_obj = CustomerSession(
        customer_id=customer_id,
        token=secrets.token_hex(32),
        created_at=now,
        expires_at=now + timedelta(hours=_TOKEN_TTL_HOURS),
        is_active=True,
    )
    db.session.add(token_obj)
    return token_obj


def _customer_json(c):
    return {
        'customerId': c.customer_id,
        'name': c.name,
        'email': c.email,
        'phone': c.phone_number,
    }


# ── POST /v1/customer/register ───────────────────────────────────

@customer_bp.route('/register', methods=['POST'])
def customer_register():
    """Create a new customer account with email/password."""
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    email = data.get('email')
    phone = data.get('phone')
    password = data.get('password')

    if not name or not email or not phone or not password:
        missing = next(f for f in ('name', 'email', 'phone', 'password') if not data.get(f))
        return jsonify({'error': 'missing_required_field',
                        'message': f'{missing} is required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'weak_password',
                        'message': 'Password must be at least 8 characters'}), 400

    if Customer.query.filter_by(email=email).first():
        return jsonify({'error': 'email_taken',
                        'message': 'Email is already in use'}), 409

    if Customer.query.filter_by(phone_number=phone).first():
        return jsonify({'error': 'phone_taken',
                        'message': 'Phone number is already in use'}), 409

    try:
        customer = Customer(
            name=name,
            email=email,
            phone_number=phone,
            account_status=AccountStatusEnum.active,
        )
        db.session.add(customer)
        db.session.flush()

        pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        db.session.add(CustomerAuth(
            customer_id=customer.customer_id,
            password_hash=pw_hash,
        ))

        token_obj = _create_customer_token(customer.customer_id)
        db.session.commit()

        return jsonify({
            'token': token_obj.token,
            'expiresAt': token_obj.expires_at.isoformat() + 'Z',
            'customer': _customer_json(customer),
        }), 201
    except Exception as exc:
        db.session.rollback()
        log_error('customer.register', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to register'}), 500


# ── POST /v1/customer/login ──────────────────────────────────────

@customer_bp.route('/login', methods=['POST'])
def customer_login():
    """Authenticate customer with email/password."""
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'error': 'missing_required_field',
                        'message': 'email and password are required'}), 400

    customer = Customer.query.filter_by(email=email).first()
    if not customer or not customer.auth:
        return jsonify({'error': 'invalid_credentials',
                        'message': 'Invalid email or password'}), 401

    if not bcrypt.checkpw(password.encode('utf-8'),
                          customer.auth.password_hash.encode('utf-8')):
        return jsonify({'error': 'invalid_credentials',
                        'message': 'Invalid email or password'}), 401

    if customer.account_status != AccountStatusEnum.active:
        return jsonify({'error': 'account_inactive',
                        'message': 'Account is not active'}), 403

    token_obj = _create_customer_token(customer.customer_id)
    db.session.commit()

    return jsonify({
        'token': token_obj.token,
        'expiresAt': token_obj.expires_at.isoformat() + 'Z',
        'customer': _customer_json(customer),
    }), 200


# ── POST /v1/customer/logout ─────────────────────────────────────

@customer_bp.route('/logout', methods=['POST'])
@customer_login_required
def customer_logout():
    """Revoke current customer session."""
    g.customer_session.is_active = False
    db.session.commit()
    return jsonify({'message': 'Logged out'}), 200


# ── GET /v1/customer/me ──────────────────────────────────────────

@customer_bp.route('/me', methods=['GET'])
@customer_login_required
def customer_me():
    """Return current customer profile."""
    return jsonify(_customer_json(g.current_customer)), 200


# ── GET /v1/customer/reservations ────────────────────────────────

@customer_bp.route('/reservations', methods=['GET'])
@customer_login_required
def customer_list_reservations():
    """List current customer's reservations."""
    include_old = request.args.get('includeOld', 'false').lower() == 'true'

    q = Reservation.query.filter_by(customer_id=g.current_customer.customer_id)
    if not include_old:
        q = q.filter(Reservation.status == ReservationStatusEnum.confirmed)

    reservations = q.order_by(Reservation.start_datetime.desc()).all()
    return jsonify([{
        'reservationId': f'R-{r.reservation_id:04d}',
        'scheduledArrival': r.start_datetime.isoformat() + 'Z',
        'endDatetime': r.end_datetime.isoformat() + 'Z',
        'status': r.status.value,
        'quotedFee': float(r.quoted_fee) if r.quoted_fee else None,
        'floorNumber': r.floor_number,
    } for r in reservations]), 200


# ── POST /v1/customer/reservations ───────────────────────────────

@customer_bp.route('/reservations', methods=['POST'])
@customer_login_required
def customer_create_reservation():
    """Self-service reservation creation for logged-in customers."""
    data = request.get_json(silent=True) or {}

    vehicle_id = data.get('vehicleId')
    scheduled_arrival = data.get('scheduledArrival')
    driver_class = data.get('driverClass', 'standard')
    garage_id = data.get('garageId')

    if not vehicle_id or not scheduled_arrival:
        missing = 'vehicleId' if not vehicle_id else 'scheduledArrival'
        return jsonify({'error': 'missing_required_field',
                        'message': f'{missing} is required'}), 400

    # Verify vehicle belongs to customer
    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle or vehicle.customer_id != g.current_customer.customer_id:
        return jsonify({'error': 'vehicle_not_found',
                        'message': 'Vehicle not found or does not belong to you'}), 404

    try:
        parsed_arrival = datetime.fromisoformat(
            scheduled_arrival.replace('Z', '+00:00')
        ).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return jsonify({'error': 'invalid_scheduled_arrival',
                        'message': 'scheduledArrival is not a valid ISO 8601 datetime'}), 400

    if parsed_arrival <= datetime.utcnow():
        return jsonify({'error': 'invalid_scheduled_arrival',
                        'message': 'scheduledArrival must be in the future'}), 400

    if driver_class not in _DRIVER_CLASS_TO_SPOT_TYPE:
        return jsonify({'error': 'invalid_driver_class',
                        'message': f'driverClass must be one of: {", ".join(sorted(_DRIVER_CLASS_TO_SPOT_TYPE.keys()))}'}), 400

    end_dt_raw = data.get('endDatetime')
    if end_dt_raw:
        try:
            end_datetime = datetime.fromisoformat(end_dt_raw.replace('Z', '+00:00')).replace(tzinfo=None)
        except (ValueError, AttributeError):
            return jsonify({'error': 'invalid_end_datetime',
                            'message': 'endDatetime is not a valid ISO 8601 datetime'}), 400
    else:
        end_datetime = parsed_arrival + timedelta(hours=2)

    try:
        spot, floor = assign_spot(
            driver_class,
            arrival_datetime=parsed_arrival,
            vehicle_type=vehicle.vehicle_type.value,
            garage_id=garage_id,
        )
    except Exception as exc:
        db.session.rollback()
        log_error('customer.create_reservation', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to assign spot'}), 500

    if not spot:
        return jsonify({'error': 'garage_full',
                        'message': 'No available spots for this driver class'}), 503

    reservation = Reservation(
        customer_id=g.current_customer.customer_id,
        vehicle_id=vehicle.vehicle_id,
        phone=g.current_customer.phone_number,
        driver_class=driver_class,
        start_datetime=parsed_arrival,
        end_datetime=end_datetime,
        garage_id=floor.garage_id,
        floor_number=floor.floor_number,
        quoted_fee=data.get('quotedFee', 0),
        status=ReservationStatusEnum.confirmed,
    )

    try:
        db.session.add(reservation)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error('customer.create_reservation', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create reservation'}), 500

    return jsonify({
        'reservationId': f'R-{reservation.reservation_id:04d}',
        'scheduledArrival': reservation.start_datetime.isoformat() + 'Z',
        'endDatetime': reservation.end_datetime.isoformat() + 'Z',
        'status': 'confirmed',
        'quotedFee': float(reservation.quoted_fee),
        'floorNumber': reservation.floor_number,
    }), 201


# ── GET /v1/customer/vehicles ────────────────────────────────────

@customer_bp.route('/vehicles', methods=['GET'])
@customer_login_required
def customer_list_vehicles():
    """List vehicles owned by the current customer."""
    vehicles = Vehicle.query.filter_by(
        customer_id=g.current_customer.customer_id
    ).all()
    return jsonify([{
        'vehicleId': v.vehicle_id,
        'licensePlate': v.license_plate,
        'plateState': v.plate_state,
        'vehicleType': v.vehicle_type.value,
    } for v in vehicles]), 200


# ── POST /v1/customer/vehicles ───────────────────────────────────

@customer_bp.route('/vehicles', methods=['POST'])
@customer_login_required
def customer_add_vehicle():
    """Add a vehicle to the current customer's account."""
    data = request.get_json(silent=True) or {}
    license_plate = data.get('licensePlate')
    plate_state = data.get('plateState', 'N/A')
    vehicle_type = data.get('vehicleType', 'car')

    if not license_plate:
        return jsonify({'error': 'missing_required_field',
                        'message': 'licensePlate is required'}), 400

    try:
        vtype = VehicleTypeEnum(vehicle_type)
    except ValueError:
        return jsonify({'error': 'invalid_vehicle_type',
                        'message': f'vehicleType must be one of: car, motorcycle, truck'}), 400

    existing = Vehicle.query.filter_by(license_plate=license_plate).first()
    if existing:
        if existing.customer_id and existing.customer_id != g.current_customer.customer_id:
            return jsonify({'error': 'plate_taken',
                            'message': 'License plate is registered to another account'}), 409
        # Claim an unclaimed vehicle
        existing.customer_id = g.current_customer.customer_id
        db.session.commit()
        return jsonify({
            'vehicleId': existing.vehicle_id,
            'licensePlate': existing.license_plate,
            'plateState': existing.plate_state,
            'vehicleType': existing.vehicle_type.value,
        }), 200

    vehicle = Vehicle(
        license_plate=license_plate,
        plate_state=plate_state,
        vehicle_type=vtype,
        customer_id=g.current_customer.customer_id,
    )
    db.session.add(vehicle)
    db.session.commit()

    return jsonify({
        'vehicleId': vehicle.vehicle_id,
        'licensePlate': vehicle.license_plate,
        'plateState': vehicle.plate_state,
        'vehicleType': vehicle.vehicle_type.value,
    }), 201
