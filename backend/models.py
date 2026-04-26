# =============================================================
#  models.py — GarageFlow Database Connection Layer
#  Task 16: Implement database connection layer
#
#  Defines all SQLAlchemy ORM models based on the table and
#  column definitions established in Tasks 2 and 3.
#
#  Import db from app.py and call db.create_all() inside an
#  app context to initialize the schema:
#
#    from app import app, db
#    with app.app_context():
#        db.create_all()
#
#  Or via the CLI shortcut in README.md:
#    python -c "from app import app, db; app.app_context().push(); db.create_all()"
# =============================================================

from datetime import datetime

from app import db
from sqlalchemy import CheckConstraint, UniqueConstraint  # Fixed: added imports for DB-level constraints
import enum


# =============================================================
#  ENUMS
#  Centralised so any model can import them and Flask-SQLAlchemy
#  maps them to database-level CHECK constraints (SQLite) or
#  native ENUM columns (PostgreSQL / MySQL).
# =============================================================

# NOTE: SpotTypeEnum values diverge from schema.sq1/Task5 which define
# (standard, handicap, staff). Python keeps (standard, accessibility, staff, eco)
# per the "Do NOT rename Enum values" rule. 'accessibility' is used throughout
# routes and tests; 'eco' maps to staff spots via _DRIVER_CLASS_TO_SPOT_TYPE.
class SpotTypeEnum(enum.Enum):
    standard      = "standard"
    accessibility = "accessibility"
    staff         = "staff"
    eco           = "eco"

class SpotStatusEnum(enum.Enum):
    available    = "available"
    occupied     = "occupied"
    out_of_order = "out_of_order"

class GateTypeEnum(enum.Enum):
    entry = "entry"
    exit  = "exit"

class GateStatusEnum(enum.Enum):
    open         = "open"
    closed       = "closed"
    out_of_order = "out_of_order"

class AccountStatusEnum(enum.Enum):
    active    = "active"
    suspended = "suspended"
    deleted   = "deleted"

class VehicleTypeEnum(enum.Enum):
    car        = "car"
    motorcycle = "motorcycle"
    truck      = "truck"

class StaffRoleEnum(enum.Enum):
    admin     = "admin"
    attendant = "attendant"

class TicketStatusEnum(enum.Enum):
    active = "active"
    closed = "closed"
    lost   = "lost"
    voided = "voided"

class PaymentMethodEnum(enum.Enum):
    cash   = "cash"
    card   = "card"
    mobile = "mobile"

class PaymentStatusEnum(enum.Enum):
    paid     = "paid"
    pending  = "pending"
    failed   = "failed"
    refunded = "refunded"

class ReservationStatusEnum(enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    fulfilled = "fulfilled"
    expired   = "expired"

class OccupancyChangeEnum(enum.Enum):
    occupied = "occupied"
    freed    = "freed"

class PricingModelEnum(enum.Enum):
    flat    = "flat"
    hourly  = "hourly"
    special = "special"


# =============================================================
#  STRUCTURE OF A GARAGE
# =============================================================

class Garage(db.Model):
    """
    Top-level definition of a parking garage.
    All floors and gates link back to a Garage record.
    """
    __tablename__ = "garage"

    # Fixed: added CheckConstraints for total_capacity > 0, number_of_floors > 0
    __table_args__ = (
        CheckConstraint('total_capacity > 0', name='chk_garage_total_capacity'),
        CheckConstraint('number_of_floors > 0', name='chk_garage_number_of_floors'),
    )

    garage_id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name              = db.Column(db.String(100), nullable=False)
    total_capacity    = db.Column(db.Integer, nullable=False)
    number_of_floors  = db.Column(db.Integer, nullable=False)
    operating_hours   = db.Column(db.String(100), nullable=False)  # Fixed: String(50)->String(100), nullable=False per schema.sq1/Task5  # WARNING: tests/test_all.py app_ctx fixture and tests/test_db_operations.py seeded_db fixture create Garage without operating_hours
    front_desk_phone  = db.Column(db.String(25))                   # Fixed: String(20)->String(25) per schema.sq1

    # Relationships
    floors      = db.relationship("Floor",     back_populates="garage", cascade="all, delete-orphan")
    gate_events = db.relationship("GateEvent", back_populates="garage", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Garage {self.garage_id}: {self.name}>"


class Floor(db.Model):
    """
    One floor within a garage.
    available_spots is updated on every entry/exit event.
    """
    __tablename__ = "floor"

    # Fixed: added CheckConstraints and UniqueConstraint per schema.sq1/Task5
    __table_args__ = (
        UniqueConstraint('garage_id', 'floor_number', name='uq_floor_per_garage'),
        CheckConstraint('floor_number >= 0', name='chk_floor_number'),
        CheckConstraint('total_spots > 0', name='chk_floor_total_spots'),
        CheckConstraint('available_spots >= 0 AND available_spots <= total_spots', name='chk_floor_available_spots'),
    )

    floor_id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    garage_id       = db.Column(db.Integer, db.ForeignKey("garage.garage_id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False)  # Fixed: added onupdate/ondelete per schema.sq1
    floor_number    = db.Column(db.Integer, nullable=False)
    floor_name      = db.Column(db.String(50), nullable=True)   # e.g. "Basement", "Roof", or null
    total_spots     = db.Column(db.Integer, nullable=False)
    available_spots = db.Column(db.Integer, nullable=False)

    # Relationships
    garage        = db.relationship("Garage",       back_populates="floors")
    parking_spots = db.relationship("ParkingSpot",  back_populates="floor", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Floor {self.floor_number} (garage {self.garage_id})>"


class ParkingSpot(db.Model):
    """
    Individual parking space on a floor.
    spot_type drives the assignment algorithm (accessibility before standard, etc.).
    """
    __tablename__ = "parking_spot"

    # Fixed: added UniqueConstraint per schema.sq1
    __table_args__ = (
        UniqueConstraint('floor_id', 'location_reference', name='uq_spot_location'),
    )

    spot_id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    floor_id           = db.Column(db.Integer, db.ForeignKey("floor.floor_id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False)  # Fixed: added onupdate/ondelete per schema.sq1
    spot_type          = db.Column(db.Enum(SpotTypeEnum, create_constraint=True, name='ck_spot_type'), nullable=False)  # Fixed: added create_constraint + name for CHECK on SQLite
    status             = db.Column(db.Enum(SpotStatusEnum, create_constraint=True, name='ck_spot_status'), nullable=False, default=SpotStatusEnum.available)  # Fixed: added create_constraint + name
    location_reference = db.Column(db.String(50), nullable=True)   # Fixed: String(20)->String(50) per schema.sq1

    # Relationships
    floor         = db.relationship("Floor",        back_populates="parking_spots")
    tickets       = db.relationship("Ticket",       back_populates="spot", cascade="all, delete-orphan")
    occupancy_log = db.relationship("OccupancyLog", back_populates="spot", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ParkingSpot {self.spot_id} [{self.spot_type.value}] — {self.status.value}>"


class GateEvent(db.Model):
    """
    Physical entry/exit gate belonging to a garage.
    Tickets reference entry_gate_id and exit_gate_id.
    """
    __tablename__ = "gate_event"

    # Fixed: added UniqueConstraint per schema.sq1
    __table_args__ = (
        UniqueConstraint('garage_id', 'gate_type', name='uq_gate_type_per_garage'),
    )

    gate_id   = db.Column(db.Integer, primary_key=True, autoincrement=True)
    garage_id = db.Column(db.Integer, db.ForeignKey("garage.garage_id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False)  # Fixed: added onupdate/ondelete per schema.sq1
    gate_type = db.Column(db.Enum(GateTypeEnum, create_constraint=True, name='ck_gate_type'),   nullable=False)  # Fixed: added create_constraint + name
    status    = db.Column(db.Enum(GateStatusEnum, create_constraint=True, name='ck_gate_status'), nullable=False, default=GateStatusEnum.closed)  # Fixed: added create_constraint + name

    # Relationships
    garage         = db.relationship("Garage", back_populates="gate_events")
    entry_tickets  = db.relationship("Ticket", foreign_keys="Ticket.entry_gate_id", back_populates="entry_gate")
    exit_tickets   = db.relationship("Ticket", foreign_keys="Ticket.exit_gate_id",  back_populates="exit_gate")

    def __repr__(self):
        return f"<GateEvent {self.gate_id} [{self.gate_type.value}] — {self.status.value}>"


# =============================================================
#  USERS
# =============================================================

class Customer(db.Model):
    """
    Registered customer account.
    Only required for reservations; walk-up tickets may have no customer record.
    Personal data (phone, name) is purged on ticket close per the data retention policy.
    """
    __tablename__ = "customer"

    customer_id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name           = db.Column(db.String(100), nullable=False)   # Fixed: nullable=True->False per schema.sq1/Task5
    email          = db.Column(db.String(120), unique=True, nullable=False)
    phone_number   = db.Column(db.String(25),  nullable=False, unique=True)  # Fixed: nullable=True->False, added unique=True, String(20)->String(25) per schema.sq1/Task5
    date_created   = db.Column(db.DateTime,    nullable=False, server_default=db.func.now())
    account_status = db.Column(db.Enum(AccountStatusEnum, create_constraint=True, name='ck_account_status'), nullable=False, default=AccountStatusEnum.active)  # Fixed: added create_constraint + name

    # Relationships
    vehicles     = db.relationship("Vehicle",     back_populates="customer")
    reservations = db.relationship("Reservation", back_populates="customer")

    def __repr__(self):
        return f"<Customer {self.customer_id}: {self.name}>"


class Vehicle(db.Model):
    """
    Vehicle associated with a ticket or reservation.
    customer_id is nullable — walk-up vehicles have no registered account.
    """
    __tablename__ = "vehicle"

    vehicle_id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    license_plate = db.Column(db.String(20),  nullable=False, unique=True)
    plate_state   = db.Column(db.String(20),  nullable=False)  # Fixed: nullable=True->False, String(50)->String(20) per schema.sq1/Task5
    vehicle_type  = db.Column(db.Enum(VehicleTypeEnum, create_constraint=True, name='ck_vehicle_type'), nullable=False)  # Fixed: added create_constraint + name
    customer_id   = db.Column(db.Integer, db.ForeignKey("customer.customer_id", onupdate="CASCADE", ondelete="SET NULL"), nullable=True)  # Fixed: added onupdate/ondelete per schema.sq1

    # Relationships
    customer     = db.relationship("Customer",    back_populates="vehicles")
    tickets      = db.relationship("Ticket",      back_populates="vehicle")
    reservations = db.relationship("Reservation", back_populates="vehicle")

    def __repr__(self):
        return f"<Vehicle {self.vehicle_id}: {self.license_plate}>"


class Staff(db.Model):
    """
    Internal operator or admin account.
    Passwords are stored as bcrypt hashes — never plaintext.
    """
    __tablename__ = "staff"

    operator_id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name           = db.Column(db.String(100), nullable=False)
    role           = db.Column(db.Enum(StaffRoleEnum, create_constraint=True, name='ck_staff_role'), nullable=False, default=StaffRoleEnum.attendant)  # Fixed: added create_constraint + name
    username       = db.Column(db.String(50),  unique=True, nullable=False)
    password_hash  = db.Column(db.String(255), nullable=False)
    is_active      = db.Column(db.Boolean, nullable=False, default=True)
    is_super_admin = db.Column(db.Boolean, nullable=False, default=False)

    # Relationships
    session_tokens = db.relationship('SessionToken', back_populates='staff', cascade='all, delete-orphan')
    system_events  = db.relationship('SystemEvent',  back_populates='staff')

    def __repr__(self):
        return f"<Staff {self.operator_id}: {self.username} [{self.role.value}]>"


class SessionToken(db.Model):
    """
    DB-backed auth token. Each row represents one active (or expired/revoked)
    session for a staff member. Tokens are 64-char hex strings with an 8-hour TTL.
    """
    __tablename__ = "session_token"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    staff_id   = db.Column(db.Integer, db.ForeignKey('staff.operator_id', ondelete='CASCADE'), nullable=False)  # CASCADE: deleting staff removes their tokens
    token      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    expires_at = db.Column(db.DateTime, nullable=False)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)

    staff = db.relationship('Staff', back_populates='session_tokens')

    def __repr__(self):
        return f"<SessionToken {self.id} staff={self.staff_id} active={self.is_active}>"


# =============================================================
#  PARKING PROCESS
# =============================================================

class Ticket(db.Model):
    """
    Represents a single parking session from entry to exit.
    Created by POST /v1/tickets; closed by PUT /v1/tickets/{id}/exit.
    duration and total_fee are null until the ticket is closed.
    """
    __tablename__ = "ticket"

    # Fixed: added CheckConstraints per schema.sq1/Task5
    __table_args__ = (
        CheckConstraint('exit_timestamp IS NULL OR exit_timestamp >= entry_timestamp', name='chk_ticket_exit_after_entry'),
        CheckConstraint('duration IS NULL OR duration >= 0', name='chk_ticket_duration'),
        CheckConstraint('total_fee IS NULL OR total_fee >= 0', name='chk_ticket_total_fee'),
    )

    ticket_id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entry_timestamp = db.Column(db.DateTime,  nullable=False, server_default=db.func.now())
    exit_timestamp  = db.Column(db.DateTime,  nullable=True)
    entry_gate_id   = db.Column(db.Integer,   db.ForeignKey("gate_event.gate_id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)  # Fixed: nullable=True->False, added onupdate/ondelete per schema.sq1/Task5
    exit_gate_id    = db.Column(db.Integer,   db.ForeignKey("gate_event.gate_id", onupdate="CASCADE", ondelete="SET NULL"), nullable=True)  # Fixed: added onupdate/ondelete per schema.sq1
    spot_id         = db.Column(db.Integer,   db.ForeignKey("parking_spot.spot_id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)  # Fixed: added onupdate/ondelete per schema.sq1
    vehicle_id      = db.Column(db.Integer,   db.ForeignKey("vehicle.vehicle_id", onupdate="CASCADE", ondelete="RESTRICT"),   nullable=False)  # Fixed: added onupdate/ondelete per schema.sq1
    status          = db.Column(db.Enum(TicketStatusEnum, create_constraint=True, name='ck_ticket_status'), nullable=False, default=TicketStatusEnum.active)  # Fixed: added create_constraint + name
    duration        = db.Column(db.Integer,   nullable=True)       # minutes, set on close
    total_fee       = db.Column(db.Numeric(10, 2), nullable=True)  # Fixed: Numeric(8,2)->Numeric(10,2) per schema.sq1
    phone           = db.Column(db.String(20), nullable=True)

    # Relationships
    spot        = db.relationship("ParkingSpot", back_populates="tickets")
    vehicle     = db.relationship("Vehicle",     back_populates="tickets")
    entry_gate  = db.relationship("GateEvent",   foreign_keys=[entry_gate_id], back_populates="entry_tickets")
    exit_gate   = db.relationship("GateEvent",   foreign_keys=[exit_gate_id],  back_populates="exit_tickets")
    payment     = db.relationship("Payment",     back_populates="ticket", uselist=False)

    def __repr__(self):
        return f"<Ticket {self.ticket_id} — {self.status.value}>"


class Payment(db.Model):
    """
    Financial transaction tied to a closed ticket.
    GarageFlow uses Stripe; raw card data is never stored here.
    """
    __tablename__ = "payment"

    # Fixed: added CheckConstraint for amount_charged >= 0 per schema.sq1
    __table_args__ = (
        CheckConstraint('amount_charged >= 0', name='chk_payment_amount'),
    )

    payment_id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ticket_id         = db.Column(db.Integer, db.ForeignKey("ticket.ticket_id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False, unique=True)  # Fixed: added onupdate/ondelete per schema.sq1
    amount_charged    = db.Column(db.Numeric(10, 2), nullable=False)  # Fixed: Numeric(8,2)->Numeric(10,2) per schema.sq1
    payment_method    = db.Column(db.Enum(PaymentMethodEnum, create_constraint=True, name='ck_payment_method'),  nullable=False)  # Fixed: added create_constraint + name
    payment_timestamp = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    payment_status    = db.Column(db.Enum(PaymentStatusEnum, create_constraint=True, name='ck_payment_status'),  nullable=False, default=PaymentStatusEnum.pending)  # Fixed: added create_constraint + name
    stripe_payment_intent_id = db.Column(db.String(100), nullable=True, unique=True)

    # Relationships
    ticket = db.relationship("Ticket", back_populates="payment")

    def __repr__(self):
        return f"<Payment {self.payment_id} [{self.payment_status.value}] — ${self.amount_charged}>"


class Reservation(db.Model):
    """
    Pre-booked parking session.
    Converted to a Ticket via PUT /v1/reservations/{id}/check on arrival.
    personal data (phone via customer) is purged immediately on cancellation
    per the data retention policy in the API design document.
    """
    __tablename__ = "reservation"

    # Fixed: added CheckConstraints per schema.sq1/Task5
    __table_args__ = (
        CheckConstraint('end_datetime > start_datetime', name='chk_reservation_time'),
        CheckConstraint('quoted_fee >= 0', name='chk_reservation_fee'),
    )

    reservation_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id    = db.Column(db.Integer, db.ForeignKey("customer.customer_id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)  # Fixed: nullable=True->False, added onupdate/ondelete per schema.sq1/Task5
    vehicle_id     = db.Column(db.Integer, db.ForeignKey("vehicle.vehicle_id", onupdate="CASCADE", ondelete="RESTRICT"),   nullable=False)  # Fixed: nullable=True->False, added onupdate/ondelete per schema.sq1/Task5
    garage_id      = db.Column(db.Integer, db.ForeignKey("garage.garage_id", onupdate="CASCADE", ondelete="SET NULL"), nullable=True)  # Multi-garage scoping
    phone          = db.Column(db.String(20),  nullable=True)
    driver_class   = db.Column(db.String(20),  nullable=True)
    floor_number   = db.Column(db.Integer,     nullable=True)
    start_datetime = db.Column(db.DateTime,  nullable=False)
    end_datetime   = db.Column(db.DateTime,  nullable=False)  # Fixed: nullable=True->False per schema.sq1/Task5
    status         = db.Column(db.Enum(ReservationStatusEnum, create_constraint=True, name='ck_reservation_status'), nullable=False, default=ReservationStatusEnum.confirmed)  # Fixed: added create_constraint + name
    created_at     = db.Column(db.DateTime,  nullable=False, server_default=db.func.now())
    quoted_fee     = db.Column(db.Numeric(10, 2), nullable=False)  # Fixed: nullable=True->False, Numeric(8,2)->Numeric(10,2) per schema.sq1/Task5

    # Relationships
    customer = db.relationship("Customer", back_populates="reservations")
    vehicle  = db.relationship("Vehicle",  back_populates="reservations")

    def __repr__(self):
        return f"<Reservation {self.reservation_id} — {self.status.value}>"


# =============================================================
#  SYSTEM ANALYTICS
# =============================================================

class OccupancyLog(db.Model):
    """
    Immutable time-series record of every spot-level status change.
    Powers the analytics endpoints (utilization, peak hours, etc.)
    and provides the live occupancy count.
    Never updated — only appended.
    """
    __tablename__ = "occupancy_log"

    log_id      = db.Column(db.Integer, primary_key=True, autoincrement=True)
    spot_id     = db.Column(db.Integer, db.ForeignKey("parking_spot.spot_id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False)  # Fixed: added onupdate/ondelete per schema.sq1
    changed_at  = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    change_type = db.Column(db.Enum(OccupancyChangeEnum, create_constraint=True, name='ck_change_type'), nullable=False)  # Fixed: added create_constraint + name

    # Relationships
    spot = db.relationship("ParkingSpot", back_populates="occupancy_log")

    def __repr__(self):
        return f"<OccupancyLog spot={self.spot_id} {self.change_type.value} @ {self.changed_at}>"


class SystemEvent(db.Model):
    """
    Audit log for any noteworthy system-level action.
    Any module can write here — gate faults, manual overrides, voided tickets, etc.
    Append-only; no record should ever be deleted in normal operation.
    """
    __tablename__ = "system_event"

    event_id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    staff_id    = db.Column(db.Integer, db.ForeignKey('staff.operator_id'), nullable=True)
    source      = db.Column(db.String(100), nullable=False)    # e.g. "ticket_module", "gate_controller"
    description = db.Column(db.Text, nullable=False)
    created_at  = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    staff = db.relationship('Staff', back_populates='system_events')

    def __repr__(self):
        return f"<SystemEvent {self.event_id} from {self.source}>"


# =============================================================
#  OPERATIONS
# =============================================================

class PricingRule(db.Model):
    """
    Defines how parking fees are calculated.
    The `program` field stores the name of the Python callable
    (resolvable at runtime) that accepts entry and exit timestamps
    and returns a Decimal fee.
    """
    __tablename__ = "pricing_rule"

    rate_id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rate_name        = db.Column(db.String(100),  nullable=False, unique=True)   # Fixed: String(50)->String(100) per schema.sq1
    applicable_hours = db.Column(db.String(100),  nullable=False)  # Fixed: String(50)->String(100), nullable=True->False per schema.sq1/Task5
    pricing_model    = db.Column(db.Enum(PricingModelEnum, create_constraint=True, name='ck_pricing_model'), nullable=False)  # Fixed: added create_constraint + name
    description      = db.Column(db.Text, nullable=False)  # Fixed: nullable=True->False per schema.sq1/Task5
    program          = db.Column(db.String(255), nullable=False)   # Fixed: String(100)->String(255), nullable=True->False per schema.sq1/Task5


# =============================================================
#  SECURITY
# =============================================================

class LoginAttempt(db.Model):
    """
    DB-backed rate limiting for login attempts.
    Tracks failed login count per IP within a sliding window.
    Survives worker restarts and works across multi-worker deployments.
    """
    __tablename__ = "login_attempt"

    ip_address   = db.Column(db.String(45), primary_key=True)  # IPv6 max length
    fail_count   = db.Column(db.Integer, nullable=False, default=0)
    window_start = db.Column(db.DateTime, nullable=False)


class ProcessedWebhookEvent(db.Model):
    """
    Idempotency table for Stripe webhook deduplication.
    The unique constraint on event_id prevents TOCTOU races when
    two identical webhook deliveries arrive simultaneously.
    """
    __tablename__ = "processed_webhook_event"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_id   = db.Column(db.String(255), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
