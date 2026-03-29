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
import enum


# =============================================================
#  ENUMS
#  Centralised so any model can import them and Flask-SQLAlchemy
#  maps them to database-level CHECK constraints (SQLite) or
#  native ENUM columns (PostgreSQL / MySQL).
# =============================================================

class SpotTypeEnum(enum.Enum):
    standard      = "standard"
    accessibility = "accessibility"
    staff         = "staff"

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

    garage_id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name              = db.Column(db.String(100), nullable=False)
    total_capacity    = db.Column(db.Integer, nullable=False)
    number_of_floors  = db.Column(db.Integer, nullable=False)
    operating_hours   = db.Column(db.String(50))          # e.g. "6:00am–midnight"
    front_desk_phone  = db.Column(db.String(20))

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

    floor_id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    garage_id       = db.Column(db.Integer, db.ForeignKey("garage.garage_id"), nullable=False)
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

    spot_id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    floor_id           = db.Column(db.Integer, db.ForeignKey("floor.floor_id"), nullable=False)
    spot_type          = db.Column(db.Enum(SpotTypeEnum), nullable=False)
    status             = db.Column(db.Enum(SpotStatusEnum), nullable=False, default=SpotStatusEnum.available)
    location_reference = db.Column(db.String(20), nullable=True)   # e.g. "A-12", "Zone B"

    # Relationships
    floor         = db.relationship("Floor",        back_populates="parking_spots")
    tickets       = db.relationship("Ticket",       back_populates="spot")
    occupancy_log = db.relationship("OccupancyLog", back_populates="spot", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ParkingSpot {self.spot_id} [{self.spot_type.value}] — {self.status.value}>"


class GateEvent(db.Model):
    """
    Physical entry/exit gate belonging to a garage.
    Tickets reference entry_gate_id and exit_gate_id.
    """
    __tablename__ = "gate_event"

    gate_id   = db.Column(db.Integer, primary_key=True, autoincrement=True)
    garage_id = db.Column(db.Integer, db.ForeignKey("garage.garage_id"), nullable=False)
    gate_type = db.Column(db.Enum(GateTypeEnum),   nullable=False)
    status    = db.Column(db.Enum(GateStatusEnum), nullable=False, default=GateStatusEnum.closed)

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
    name           = db.Column(db.String(100), nullable=False)
    email          = db.Column(db.String(120), unique=True, nullable=False)
    phone_number   = db.Column(db.String(20),  nullable=False)
    date_created   = db.Column(db.DateTime,    nullable=False, server_default=db.func.now())
    account_status = db.Column(db.Enum(AccountStatusEnum), nullable=False, default=AccountStatusEnum.active)

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
    plate_state   = db.Column(db.String(50),  nullable=True)
    vehicle_type  = db.Column(db.Enum(VehicleTypeEnum), nullable=False)
    customer_id   = db.Column(db.Integer, db.ForeignKey("customer.customer_id"), nullable=True)

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

    operator_id   = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name          = db.Column(db.String(100), nullable=False)
    role          = db.Column(db.Enum(StaffRoleEnum), nullable=False, default=StaffRoleEnum.attendant)
    username      = db.Column(db.String(50),  unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active     = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self):
        return f"<Staff {self.operator_id}: {self.username} [{self.role.value}]>"


class SessionToken(db.Model):
    """
    DB-backed auth token. Each row represents one active (or expired/revoked)
    session for a staff member. Tokens are 64-char hex strings with an 8-hour TTL.
    """
    __tablename__ = "session_token"

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    staff_id   = db.Column(db.Integer, db.ForeignKey('staff.operator_id'), nullable=False)
    token      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)

    staff = db.relationship('Staff', backref='session_tokens')

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

    ticket_id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entry_timestamp = db.Column(db.DateTime,  nullable=False, server_default=db.func.now())
    exit_timestamp  = db.Column(db.DateTime,  nullable=True)
    entry_gate_id   = db.Column(db.Integer,   db.ForeignKey("gate_event.gate_id"), nullable=True)
    exit_gate_id    = db.Column(db.Integer,   db.ForeignKey("gate_event.gate_id"), nullable=True)
    spot_id         = db.Column(db.Integer,   db.ForeignKey("parking_spot.spot_id"), nullable=False)
    vehicle_id      = db.Column(db.Integer,   db.ForeignKey("vehicle.vehicle_id"),   nullable=False)
    status          = db.Column(db.Enum(TicketStatusEnum), nullable=False, default=TicketStatusEnum.active)
    duration        = db.Column(db.Integer,   nullable=True)       # minutes, set on close
    total_fee       = db.Column(db.Numeric(8, 2), nullable=True)   # set on close
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

    payment_id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ticket_id         = db.Column(db.Integer, db.ForeignKey("ticket.ticket_id"), nullable=False, unique=True)
    amount_charged    = db.Column(db.Numeric(8, 2), nullable=False)
    payment_method    = db.Column(db.Enum(PaymentMethodEnum),  nullable=False)
    payment_timestamp = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    payment_status    = db.Column(db.Enum(PaymentStatusEnum),  nullable=False, default=PaymentStatusEnum.pending)
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

    reservation_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id    = db.Column(db.Integer, db.ForeignKey("customer.customer_id"), nullable=True)
    vehicle_id     = db.Column(db.Integer, db.ForeignKey("vehicle.vehicle_id"),   nullable=True)
    phone          = db.Column(db.String(20),  nullable=True)
    floor_number   = db.Column(db.Integer,     nullable=True)
    start_datetime = db.Column(db.DateTime,  nullable=False)
    end_datetime   = db.Column(db.DateTime,  nullable=True)
    status         = db.Column(db.Enum(ReservationStatusEnum), nullable=False, default=ReservationStatusEnum.confirmed)
    created_at     = db.Column(db.DateTime,  nullable=False, server_default=db.func.now())
    quoted_fee     = db.Column(db.Numeric(8, 2), nullable=True)

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
    spot_id     = db.Column(db.Integer, db.ForeignKey("parking_spot.spot_id"), nullable=False)
    changed_at  = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    change_type = db.Column(db.Enum(OccupancyChangeEnum), nullable=False)

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

    staff = db.relationship('Staff', backref='system_events')

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
    rate_name        = db.Column(db.String(50),  nullable=False, unique=True)   # e.g. "standard", "weekend"
    applicable_hours = db.Column(db.String(50),  nullable=True)
    pricing_model    = db.Column(db.Enum(PricingModelEnum), nullable=False)
    description      = db.Column(db.Text, nullable=True)
    program          = db.Column(db.String(100), nullable=True)   # e.g. "pricing.standard_rate"

    def __repr__(self):
        return f"<PricingRule {self.rate_id}: {self.rate_name} [{self.pricing_model.value}]>"
