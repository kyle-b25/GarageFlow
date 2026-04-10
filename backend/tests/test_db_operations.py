"""
tests/test_db_operations.py — Database-level constraint and integrity tests

Complements the groupmate's existing files:
  - db/test_database_operations.sql — happy-path CRUD (INSERT/SELECT/UPDATE/DELETE)
    across all 14 tables wrapped in a ROLLBACK transaction. No error cases.
  - backend/seed_and_indexes.sql  — index creation + data-verification queries.

This file adds what those files do NOT cover:
  A. Foreign key constraint violations & ORM cascade behaviour
  B. ENUM enforcement (xfail on SQLite — SQLAlchemy 2.0 maps Enum to VARCHAR
     without CHECK constraints; DB-level enforcement requires MySQL native ENUMs)
  C. CHECK constraint enforcement (schema.sq1 only — xfail gaps)
  D. UNIQUE constraint enforcement (single-column + composite gap docs)
  E. Index existence verification
  F. Concurrent access / race conditions (MySQL-only)
  G. Data integrity edge cases (NULLs, defaults, precision)

Usage:
    pytest tests/test_db_operations.py -v
    pytest tests/test_db_operations.py -v -k "not concurrent"   # skip MySQL-only
    TEST_DB_URL=mysql+pymysql://u:p@host/db pytest tests/test_db_operations.py -v
"""

import os
import secrets
import threading
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, text, inspect
from sqlalchemy.exc import IntegrityError, StatementError

from app import app, db
from models import (
    Garage, Floor, ParkingSpot, GateEvent,
    Customer, Vehicle, Staff, SessionToken,
    Ticket, Payment, Reservation,
    OccupancyLog, SystemEvent, PricingRule,
    SpotTypeEnum, SpotStatusEnum, GateTypeEnum, GateStatusEnum,
    AccountStatusEnum, VehicleTypeEnum, StaffRoleEnum,
    TicketStatusEnum, PaymentMethodEnum, PaymentStatusEnum,
    ReservationStatusEnum, OccupancyChangeEnum, PricingModelEnum,
)

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

requires_mysql = pytest.mark.skipif(
    not os.environ.get("TEST_DB_URL")
    or os.environ.get("TEST_DB_URL", "").startswith("sqlite"),
    reason="Requires MySQL/MariaDB — set TEST_DB_URL env var",
)

schema_gap = pytest.mark.xfail(
    reason=(
        "models.py does not define this constraint; "
        "only NoneCodeDeliverables/schema.sq1 (MySQL DDL) does"
    ),
    strict=False,
)

enum_no_check = pytest.mark.xfail(
    reason=(
        "SQLAlchemy 2.0 maps Enum to VARCHAR on SQLite without CHECK constraints. "
        "DB-level ENUM enforcement requires MySQL native ENUMs or "
        "create_constraint=True on each db.Enum() column in models.py"
    ),
    strict=False,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """
    App context + db.session with SQLite foreign-key enforcement enabled.

    Registers a DBAPI-level listener that runs ``PRAGMA foreign_keys = ON``
    on every new raw connection so that FK violations raise IntegrityError
    even on SQLite (which disables FK checks by default).
    """
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
    app.config["SECRET_KEY"] = "test-secret"

    with app.app_context():
        # Register FK enforcement listener
        @event.listens_for(db.engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        # Dispose existing pool so new connections pick up the listener
        db.engine.dispose()

        db.create_all()
        yield db.session
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def seeded_db(db_session):
    """
    Pre-loaded data chain for tests that need existing rows to reference.

    Creates:
        1 Garage  →  1 Floor (total_spots=5, available_spots=3)
                      →  2 ParkingSpots (1 standard available, 1 standard occupied)
                  →  2 GateEvents (entry + exit)
        1 Customer  →  1 Vehicle
        1 Staff
        1 Ticket (active, on occupied spot)
        1 OccupancyLog

    Returns a dict with all entity IDs.
    """
    garage = Garage(name="Test Garage", total_capacity=5, number_of_floors=1)
    db_session.add(garage)
    db_session.flush()

    floor = Floor(
        garage_id=garage.garage_id,
        floor_number=1,
        total_spots=5,
        available_spots=3,
    )
    db_session.add(floor)
    db_session.flush()

    spot_avail = ParkingSpot(
        floor_id=floor.floor_id,
        spot_type=SpotTypeEnum.standard,
        status=SpotStatusEnum.available,
    )
    spot_occ = ParkingSpot(
        floor_id=floor.floor_id,
        spot_type=SpotTypeEnum.standard,
        status=SpotStatusEnum.occupied,
    )
    db_session.add_all([spot_avail, spot_occ])
    db_session.flush()

    entry_gate = GateEvent(
        garage_id=garage.garage_id,
        gate_type=GateTypeEnum.entry,
        status=GateStatusEnum.open,
    )
    exit_gate = GateEvent(
        garage_id=garage.garage_id,
        gate_type=GateTypeEnum.exit,
        status=GateStatusEnum.open,
    )
    db_session.add_all([entry_gate, exit_gate])
    db_session.flush()

    customer = Customer(
        name="Test Driver",
        email="driver@test.com",
        phone_number="555-0100",
        account_status=AccountStatusEnum.active,
    )
    db_session.add(customer)
    db_session.flush()

    vehicle = Vehicle(
        license_plate="TEST-001",
        plate_state="NY",
        vehicle_type=VehicleTypeEnum.car,
        customer_id=customer.customer_id,
    )
    db_session.add(vehicle)
    db_session.flush()

    staff = Staff(
        name="Seed Admin",
        username="seed_admin",
        password_hash="fakehash",
        role=StaffRoleEnum.admin,
    )
    db_session.add(staff)
    db_session.flush()

    ticket = Ticket(
        spot_id=spot_occ.spot_id,
        vehicle_id=vehicle.vehicle_id,
        entry_gate_id=entry_gate.gate_id,
        status=TicketStatusEnum.active,
    )
    db_session.add(ticket)
    db_session.flush()

    occ_log = OccupancyLog(
        spot_id=spot_occ.spot_id,
        change_type=OccupancyChangeEnum.occupied,
    )
    db_session.add(occ_log)
    db_session.commit()

    return {
        "garage_id": garage.garage_id,
        "floor_id": floor.floor_id,
        "spot_avail_id": spot_avail.spot_id,
        "spot_occ_id": spot_occ.spot_id,
        "entry_gate_id": entry_gate.gate_id,
        "exit_gate_id": exit_gate.gate_id,
        "customer_id": customer.customer_id,
        "vehicle_id": vehicle.vehicle_id,
        "staff_id": staff.operator_id,
        "ticket_id": ticket.ticket_id,
        "occ_log_id": occ_log.log_id,
    }


# ===================================================================
#  A. Foreign Key Constraint Violations
# ===================================================================


class TestForeignKeyConstraints:
    """Verify that the DB rejects rows with nonexistent FK references
    and that ORM-level cascades propagate deletes correctly."""

    # --- FK violation tests (expect IntegrityError) ---

    def test_floor_nonexistent_garage_id(self, db_session):
        """Floor with garage_id=9999 must be rejected (no such garage)."""
        db_session.add(
            Floor(garage_id=9999, floor_number=1, total_spots=5, available_spots=5)
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_parking_spot_nonexistent_floor_id(self, db_session):
        """ParkingSpot with floor_id=9999 must be rejected."""
        db_session.add(
            ParkingSpot(
                floor_id=9999,
                spot_type=SpotTypeEnum.standard,
                status=SpotStatusEnum.available,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_gate_event_nonexistent_garage_id(self, db_session):
        """GateEvent with garage_id=9999 must be rejected."""
        db_session.add(
            GateEvent(
                garage_id=9999,
                gate_type=GateTypeEnum.entry,
                status=GateStatusEnum.closed,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_vehicle_nonexistent_customer_id(self, db_session):
        """Vehicle with customer_id=9999 must be rejected (no such customer)."""
        db_session.add(
            Vehicle(
                license_plate="BAD-FK-01",
                vehicle_type=VehicleTypeEnum.car,
                customer_id=9999,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_ticket_nonexistent_spot_id(self, seeded_db):
        """Ticket with spot_id=9999 must be rejected."""
        db.session.add(
            Ticket(
                spot_id=9999,
                vehicle_id=seeded_db["vehicle_id"],
                status=TicketStatusEnum.active,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_ticket_nonexistent_vehicle_id(self, seeded_db):
        """Ticket with vehicle_id=9999 must be rejected."""
        db.session.add(
            Ticket(
                spot_id=seeded_db["spot_avail_id"],
                vehicle_id=9999,
                status=TicketStatusEnum.active,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_ticket_nonexistent_entry_gate_id(self, seeded_db):
        """Ticket with entry_gate_id=9999 must be rejected."""
        db.session.add(
            Ticket(
                spot_id=seeded_db["spot_avail_id"],
                vehicle_id=seeded_db["vehicle_id"],
                entry_gate_id=9999,
                status=TicketStatusEnum.active,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_payment_nonexistent_ticket_id(self, db_session):
        """Payment with ticket_id=9999 must be rejected."""
        db_session.add(
            Payment(
                ticket_id=9999,
                amount_charged=Decimal("10.00"),
                payment_method=PaymentMethodEnum.cash,
                payment_status=PaymentStatusEnum.paid,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_reservation_nonexistent_customer_id(self, db_session):
        """Reservation with customer_id=9999 must be rejected."""
        db_session.add(
            Reservation(
                customer_id=9999,
                start_datetime=datetime.utcnow(),
                status=ReservationStatusEnum.confirmed,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_reservation_nonexistent_vehicle_id(self, seeded_db):
        """Reservation with vehicle_id=9999 must be rejected."""
        db.session.add(
            Reservation(
                customer_id=seeded_db["customer_id"],
                vehicle_id=9999,
                start_datetime=datetime.utcnow(),
                status=ReservationStatusEnum.confirmed,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_occupancy_log_nonexistent_spot_id(self, db_session):
        """OccupancyLog with spot_id=9999 must be rejected."""
        db_session.add(
            OccupancyLog(
                spot_id=9999,
                change_type=OccupancyChangeEnum.occupied,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_session_token_nonexistent_staff_id(self, db_session):
        """SessionToken with staff_id=9999 must be rejected."""
        db_session.add(
            SessionToken(
                staff_id=9999,
                token=secrets.token_hex(32),
                expires_at=datetime.utcnow() + timedelta(hours=8),
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    # --- ORM cascade tests ---

    def test_cascade_delete_garage_removes_floors(self, seeded_db):
        """Deleting a Garage must cascade-delete its Floors (ORM cascade)."""
        garage = db.session.get(Garage, seeded_db["garage_id"])
        db.session.delete(garage)
        db.session.flush()
        assert db.session.get(Floor, seeded_db["floor_id"]) is None

    def test_cascade_delete_floor_removes_spots(self, seeded_db):
        """Deleting a Floor must cascade-delete its ParkingSpots."""
        floor = db.session.get(Floor, seeded_db["floor_id"])
        db.session.delete(floor)
        db.session.flush()
        assert db.session.get(ParkingSpot, seeded_db["spot_avail_id"]) is None
        assert db.session.get(ParkingSpot, seeded_db["spot_occ_id"]) is None

    def test_cascade_delete_spot_removes_occupancy_logs(self, seeded_db):
        """Deleting a ParkingSpot must cascade-delete its OccupancyLogs."""
        spot = db.session.get(ParkingSpot, seeded_db["spot_occ_id"])
        db.session.delete(spot)
        db.session.flush()
        assert db.session.get(OccupancyLog, seeded_db["occ_log_id"]) is None

    def test_cascade_delete_staff_removes_session_tokens(self, seeded_db):
        """Deleting a Staff must cascade-delete its SessionTokens."""
        staff = db.session.get(Staff, seeded_db["staff_id"])
        token = SessionToken(
            staff_id=staff.operator_id,
            token=secrets.token_hex(32),
            expires_at=datetime.utcnow() + timedelta(hours=8),
            is_active=True,
        )
        db.session.add(token)
        db.session.flush()
        token_id = token.id

        db.session.delete(staff)
        db.session.flush()
        assert db.session.get(SessionToken, token_id) is None


# ===================================================================
#  B. ENUM Enforcement
# ===================================================================


class TestEnumEnforcement:
    """Verify that the database rejects invalid ENUM values.

    Raw SQL is used to bypass Python enum validation so the test targets
    the actual DB constraint.  On MySQL this would be a native ENUM column;
    on SQLite, SQLAlchemy 2.0 maps Enum to VARCHAR **without** CHECK
    constraints (create_constraint defaults to False).  All tests are
    therefore marked @enum_no_check on SQLite — they document the gap and
    will pass when run against MySQL or if create_constraint=True is added
    to models.py.
    """

    @enum_no_check
    def test_spot_type_rejects_invalid(self, seeded_db):
        """parking_spot.spot_type = 'vip' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO parking_spot (floor_id, spot_type, status) "
                    "VALUES (:fid, 'vip', 'available')"
                ),
                {"fid": seeded_db["floor_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_spot_status_rejects_invalid(self, seeded_db):
        """parking_spot.status = 'dirty' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO parking_spot (floor_id, spot_type, status) "
                    "VALUES (:fid, 'standard', 'dirty')"
                ),
                {"fid": seeded_db["floor_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_gate_type_rejects_invalid(self, seeded_db):
        """gate_event.gate_type = 'both' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO gate_event (garage_id, gate_type, status) "
                    "VALUES (:gid, 'both', 'closed')"
                ),
                {"gid": seeded_db["garage_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_gate_status_rejects_invalid(self, seeded_db):
        """gate_event.status = 'locked' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO gate_event (garage_id, gate_type, status) "
                    "VALUES (:gid, 'entry', 'locked')"
                ),
                {"gid": seeded_db["garage_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_account_status_rejects_invalid(self, db_session):
        """customer.account_status = 'banned' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db_session.execute(
                text(
                    "INSERT INTO customer (name, email, phone_number, account_status) "
                    "VALUES ('X', 'x@test.com', '555', 'banned')"
                )
            )
            db_session.flush()
        db_session.rollback()

    @enum_no_check
    def test_vehicle_type_rejects_invalid(self, db_session):
        """vehicle.vehicle_type = 'bicycle' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db_session.execute(
                text(
                    "INSERT INTO vehicle (license_plate, vehicle_type) "
                    "VALUES ('BIKE-001', 'bicycle')"
                )
            )
            db_session.flush()
        db_session.rollback()

    @enum_no_check
    def test_staff_role_rejects_invalid(self, db_session):
        """staff.role = 'manager' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db_session.execute(
                text(
                    "INSERT INTO staff (name, role, username, password_hash, is_active) "
                    "VALUES ('X', 'manager', 'mgr1', 'hash', 1)"
                )
            )
            db_session.flush()
        db_session.rollback()

    @enum_no_check
    def test_ticket_status_rejects_invalid(self, seeded_db):
        """ticket.status = 'expired' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO ticket (spot_id, vehicle_id, status) "
                    "VALUES (:sid, :vid, 'expired')"
                ),
                {
                    "sid": seeded_db["spot_avail_id"],
                    "vid": seeded_db["vehicle_id"],
                },
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_payment_method_rejects_invalid(self, seeded_db):
        """payment.payment_method = 'crypto' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO payment (ticket_id, amount_charged, payment_method, payment_status) "
                    "VALUES (:tid, 10.00, 'crypto', 'paid')"
                ),
                {"tid": seeded_db["ticket_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_payment_status_rejects_invalid(self, seeded_db):
        """payment.payment_status = 'disputed' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO payment (ticket_id, amount_charged, payment_method, payment_status) "
                    "VALUES (:tid, 10.00, 'cash', 'disputed')"
                ),
                {"tid": seeded_db["ticket_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_reservation_status_rejects_invalid(self, db_session):
        """reservation.status = 'pending' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db_session.execute(
                text(
                    "INSERT INTO reservation (start_datetime, status) "
                    "VALUES ('2025-01-01 10:00:00', 'pending')"
                )
            )
            db_session.flush()
        db_session.rollback()

    @enum_no_check
    def test_occupancy_change_rejects_invalid(self, seeded_db):
        """occupancy_log.change_type = 'partial' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db.session.execute(
                text(
                    "INSERT INTO occupancy_log (spot_id, change_type) "
                    "VALUES (:sid, 'partial')"
                ),
                {"sid": seeded_db["spot_occ_id"]},
            )
            db.session.flush()
        db.session.rollback()

    @enum_no_check
    def test_pricing_model_rejects_invalid(self, db_session):
        """pricing_rule.pricing_model = 'dynamic' must be rejected by the DB."""
        with pytest.raises((IntegrityError, StatementError)):
            db_session.execute(
                text(
                    "INSERT INTO pricing_rule (rate_name, pricing_model) "
                    "VALUES ('bad_rule', 'dynamic')"
                )
            )
            db_session.flush()
        db_session.rollback()


# ===================================================================
#  C. CHECK Constraint Enforcement
# ===================================================================


class TestCheckConstraints:
    """CHECK constraints from NoneCodeDeliverables/schema.sq1 that are NOT
    defined in backend/models.py.

    When tests run against SQLite via db.create_all() (from models.py),
    these constraints do not exist.  All tests are marked @schema_gap
    (xfail) to document the gap.  They will start passing if/when
    CheckConstraint objects are added to models.py.
    """

    @schema_gap
    def test_garage_capacity_must_be_positive(self, db_session):
        """schema.sq1: CHECK (total_capacity > 0).  models.py: absent."""
        db_session.add(Garage(name="Bad", total_capacity=-1, number_of_floors=1))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    @schema_gap
    def test_garage_floors_must_be_positive(self, db_session):
        """schema.sq1: CHECK (number_of_floors > 0).  models.py: absent."""
        db_session.add(Garage(name="Bad", total_capacity=10, number_of_floors=0))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    @schema_gap
    def test_floor_number_non_negative(self, seeded_db):
        """schema.sq1: CHECK (floor_number >= 0).  models.py: absent."""
        db.session.add(
            Floor(
                garage_id=seeded_db["garage_id"],
                floor_number=-1,
                total_spots=5,
                available_spots=5,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_floor_total_spots_positive(self, seeded_db):
        """schema.sq1: CHECK (total_spots > 0).  models.py: absent."""
        db.session.add(
            Floor(
                garage_id=seeded_db["garage_id"],
                floor_number=99,
                total_spots=0,
                available_spots=0,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_floor_available_spots_non_negative(self, seeded_db):
        """schema.sq1: CHECK (available_spots >= 0).  models.py: absent."""
        db.session.add(
            Floor(
                garage_id=seeded_db["garage_id"],
                floor_number=99,
                total_spots=5,
                available_spots=-1,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_floor_available_not_exceed_total(self, seeded_db):
        """schema.sq1: CHECK (available_spots <= total_spots).  models.py: absent."""
        db.session.add(
            Floor(
                garage_id=seeded_db["garage_id"],
                floor_number=99,
                total_spots=5,
                available_spots=10,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_ticket_exit_not_before_entry(self, seeded_db):
        """schema.sq1: CHECK (exit_timestamp IS NULL OR exit_timestamp >= entry_timestamp).
        models.py: absent."""
        now = datetime.utcnow()
        db.session.add(
            Ticket(
                spot_id=seeded_db["spot_avail_id"],
                vehicle_id=seeded_db["vehicle_id"],
                entry_timestamp=now,
                exit_timestamp=now - timedelta(hours=2),
                status=TicketStatusEnum.closed,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_ticket_duration_non_negative(self, seeded_db):
        """schema.sq1: CHECK (duration IS NULL OR duration >= 0).  models.py: absent."""
        db.session.add(
            Ticket(
                spot_id=seeded_db["spot_avail_id"],
                vehicle_id=seeded_db["vehicle_id"],
                status=TicketStatusEnum.active,
                duration=-10,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_ticket_fee_non_negative(self, seeded_db):
        """schema.sq1: CHECK (total_fee IS NULL OR total_fee >= 0).  models.py: absent."""
        db.session.add(
            Ticket(
                spot_id=seeded_db["spot_avail_id"],
                vehicle_id=seeded_db["vehicle_id"],
                status=TicketStatusEnum.active,
                total_fee=Decimal("-5.00"),
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_reservation_end_after_start(self, seeded_db):
        """schema.sq1: CHECK (end_datetime > start_datetime).  models.py: absent."""
        now = datetime.utcnow()
        db.session.add(
            Reservation(
                customer_id=seeded_db["customer_id"],
                vehicle_id=seeded_db["vehicle_id"],
                start_datetime=now,
                end_datetime=now - timedelta(hours=1),
                status=ReservationStatusEnum.confirmed,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_reservation_fee_non_negative(self, seeded_db):
        """schema.sq1: CHECK (quoted_fee >= 0).  models.py: absent."""
        db.session.add(
            Reservation(
                customer_id=seeded_db["customer_id"],
                vehicle_id=seeded_db["vehicle_id"],
                start_datetime=datetime.utcnow(),
                end_datetime=datetime.utcnow() + timedelta(hours=2),
                status=ReservationStatusEnum.confirmed,
                quoted_fee=Decimal("-10.00"),
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_payment_amount_non_negative(self, seeded_db):
        """schema.sq1: CHECK (amount_charged >= 0).  models.py: absent."""
        db.session.add(
            Payment(
                ticket_id=seeded_db["ticket_id"],
                amount_charged=Decimal("-5.00"),
                payment_method=PaymentMethodEnum.cash,
                payment_status=PaymentStatusEnum.paid,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


# ===================================================================
#  D. UNIQUE Constraint Enforcement
# ===================================================================


class TestUniqueConstraints:
    """Verify single-column UNIQUE constraints defined in models.py and
    document composite-UNIQUE gaps from schema.sq1."""

    def test_customer_email_unique(self, db_session):
        """Duplicate customer.email must be rejected (unique=True in models.py)."""
        c1 = Customer(email="dupe@test.com", account_status=AccountStatusEnum.active)
        c2 = Customer(email="dupe@test.com", account_status=AccountStatusEnum.active)
        db_session.add(c1)
        db_session.flush()
        db_session.add(c2)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    @schema_gap
    def test_customer_phone_unique(self, db_session):
        """schema.sq1: UNIQUE (phone_number).  models.py: no unique constraint.

        GAP: Two customers with the same phone_number can be inserted.
        """
        c1 = Customer(
            email="a@test.com",
            phone_number="555-DUPE",
            account_status=AccountStatusEnum.active,
        )
        c2 = Customer(
            email="b@test.com",
            phone_number="555-DUPE",
            account_status=AccountStatusEnum.active,
        )
        db_session.add_all([c1, c2])
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_vehicle_plate_unique(self, db_session):
        """Duplicate vehicle.license_plate must be rejected."""
        v1 = Vehicle(license_plate="DUPE-01", vehicle_type=VehicleTypeEnum.car)
        v2 = Vehicle(license_plate="DUPE-01", vehicle_type=VehicleTypeEnum.truck)
        db_session.add(v1)
        db_session.flush()
        db_session.add(v2)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_staff_username_unique(self, db_session):
        """Duplicate staff.username must be rejected."""
        s1 = Staff(
            name="A", username="dupe_user", password_hash="h1", role=StaffRoleEnum.admin
        )
        s2 = Staff(
            name="B",
            username="dupe_user",
            password_hash="h2",
            role=StaffRoleEnum.attendant,
        )
        db_session.add(s1)
        db_session.flush()
        db_session.add(s2)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_payment_ticket_id_unique(self, seeded_db):
        """Two payments for the same ticket must be rejected (one-to-one)."""
        p1 = Payment(
            ticket_id=seeded_db["ticket_id"],
            amount_charged=Decimal("10.00"),
            payment_method=PaymentMethodEnum.cash,
            payment_status=PaymentStatusEnum.paid,
        )
        db.session.add(p1)
        db.session.flush()

        p2 = Payment(
            ticket_id=seeded_db["ticket_id"],
            amount_charged=Decimal("15.00"),
            payment_method=PaymentMethodEnum.card,
            payment_status=PaymentStatusEnum.pending,
        )
        db.session.add(p2)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_payment_stripe_intent_unique(self, seeded_db):
        """Duplicate stripe_payment_intent_id must be rejected."""
        # Need a second ticket for the second payment (ticket_id is also unique)
        v2 = Vehicle(license_plate="UNIQ-02", vehicle_type=VehicleTypeEnum.car)
        db.session.add(v2)
        db.session.flush()
        t2 = Ticket(
            spot_id=seeded_db["spot_avail_id"],
            vehicle_id=v2.vehicle_id,
            status=TicketStatusEnum.active,
        )
        db.session.add(t2)
        db.session.flush()

        p1 = Payment(
            ticket_id=seeded_db["ticket_id"],
            amount_charged=Decimal("10.00"),
            payment_method=PaymentMethodEnum.card,
            payment_status=PaymentStatusEnum.paid,
            stripe_payment_intent_id="pi_duplicate_test",
        )
        db.session.add(p1)
        db.session.flush()

        p2 = Payment(
            ticket_id=t2.ticket_id,
            amount_charged=Decimal("20.00"),
            payment_method=PaymentMethodEnum.card,
            payment_status=PaymentStatusEnum.paid,
            stripe_payment_intent_id="pi_duplicate_test",
        )
        db.session.add(p2)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_pricing_rule_name_unique(self, db_session):
        """Duplicate pricing_rule.rate_name must be rejected."""
        r1 = PricingRule(rate_name="standard", pricing_model=PricingModelEnum.hourly)
        r2 = PricingRule(rate_name="standard", pricing_model=PricingModelEnum.flat)
        db_session.add(r1)
        db_session.flush()
        db_session.add(r2)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_stripe_intent_null_not_unique(self, seeded_db):
        """Two payments with stripe_payment_intent_id=NULL should both succeed.

        SQL standard: NULL != NULL for UNIQUE constraints.
        """
        v2 = Vehicle(license_plate="NULL-02", vehicle_type=VehicleTypeEnum.car)
        db.session.add(v2)
        db.session.flush()
        t2 = Ticket(
            spot_id=seeded_db["spot_avail_id"],
            vehicle_id=v2.vehicle_id,
            status=TicketStatusEnum.active,
        )
        db.session.add(t2)
        db.session.flush()

        p1 = Payment(
            ticket_id=seeded_db["ticket_id"],
            amount_charged=Decimal("10.00"),
            payment_method=PaymentMethodEnum.cash,
            payment_status=PaymentStatusEnum.paid,
            stripe_payment_intent_id=None,
        )
        p2 = Payment(
            ticket_id=t2.ticket_id,
            amount_charged=Decimal("15.00"),
            payment_method=PaymentMethodEnum.cash,
            payment_status=PaymentStatusEnum.paid,
            stripe_payment_intent_id=None,
        )
        db.session.add_all([p1, p2])
        db.session.flush()  # should NOT raise

    # --- Composite UNIQUE gaps ---

    @schema_gap
    def test_floor_composite_unique_gap(self, seeded_db):
        """schema.sq1: UNIQUE (garage_id, floor_number).  models.py: absent.

        GAP: Two floors with the same garage_id + floor_number can be inserted.
        """
        f1 = Floor(
            garage_id=seeded_db["garage_id"],
            floor_number=1,
            total_spots=5,
            available_spots=5,
        )
        db.session.add(f1)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_gate_composite_unique_gap(self, seeded_db):
        """schema.sq1: UNIQUE (garage_id, gate_type).  models.py: absent.

        GAP: Two entry gates for the same garage can be inserted.
        """
        g2 = GateEvent(
            garage_id=seeded_db["garage_id"],
            gate_type=GateTypeEnum.entry,
            status=GateStatusEnum.closed,
        )
        db.session.add(g2)
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    @schema_gap
    def test_spot_location_composite_unique_gap(self, seeded_db):
        """schema.sq1: UNIQUE (floor_id, location_reference).  models.py: absent.

        GAP: Two spots on the same floor with identical location_reference
        can be inserted.
        """
        s1 = ParkingSpot(
            floor_id=seeded_db["floor_id"],
            spot_type=SpotTypeEnum.standard,
            status=SpotStatusEnum.available,
            location_reference="A-1",
        )
        s2 = ParkingSpot(
            floor_id=seeded_db["floor_id"],
            spot_type=SpotTypeEnum.standard,
            status=SpotStatusEnum.available,
            location_reference="A-1",
        )
        db.session.add_all([s1, s2])
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


# ===================================================================
#  E. Index Existence and Performance
# ===================================================================


class TestIndexExistence:
    """Verify that expected indexes exist in the database schema."""

    def test_sqlalchemy_indexes_exist(self, db_session):
        """Verify ORM-created indexes are present in SQLite.

        SQLAlchemy auto-creates indexes for columns with index=True and
        unique=True.  The 10 named indexes from seed_and_indexes.sql
        (idx_floor_garage_id, etc.) only exist when that script is run
        against MySQL — they are NOT part of db.create_all().
        """
        insp = inspect(db.engine)

        # session_token.token has index=True → expect an index
        st_indexes = insp.get_indexes("session_token")
        st_cols = {tuple(sorted(idx["column_names"])) for idx in st_indexes}
        assert ("token",) in st_cols, (
            "Expected index on session_token.token (index=True in models.py)"
        )

        # UNIQUE columns also generate indexes: customer.email, vehicle.license_plate,
        # staff.username, payment.ticket_id, pricing_rule.rate_name
        for table, col in [
            ("customer", "email"),
            ("vehicle", "license_plate"),
            ("staff", "username"),
            ("payment", "ticket_id"),
            ("pricing_rule", "rate_name"),
        ]:
            uqs = insp.get_unique_constraints(table)
            uq_cols = {tuple(sorted(c["column_names"])) for c in uqs}
            indexes = insp.get_indexes(table)
            idx_cols = {tuple(sorted(i["column_names"])) for i in indexes}
            # Either a unique constraint or a unique index should cover this column
            assert (col,) in uq_cols or (col,) in idx_cols, (
                f"Expected unique index/constraint on {table}.{col}"
            )

    @requires_mysql
    def test_named_indexes_exist_mysql(self, db_session):
        """Query information_schema.STATISTICS for the 10 named indexes
        from seed_and_indexes.sql and verify EXPLAIN uses index scans.

        Only runnable against MySQL/MariaDB.
        """
        expected_indexes = [
            "idx_floor_garage_id",
            "idx_parking_spot_floor_id",
            "idx_vehicle_customer_id",
            "idx_ticket_vehicle_id",
            "idx_ticket_spot_id",
            "idx_ticket_entry_gate_id",
            "idx_ticket_exit_gate_id",
            "idx_reservation_customer_id",
            "idx_reservation_vehicle_id",
            "idx_occupancy_log_spot_id",
        ]

        result = db.session.execute(
            text(
                "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "GROUP BY INDEX_NAME"
            )
        )
        existing = {row[0] for row in result}

        for idx_name in expected_indexes:
            assert idx_name in existing, f"Index {idx_name} not found in MySQL schema"

        # EXPLAIN checks — verify index usage on 3 queries
        for query in [
            "EXPLAIN SELECT * FROM ticket WHERE vehicle_id = 1",
            "EXPLAIN SELECT * FROM reservation WHERE customer_id = 1",
            "EXPLAIN SELECT * FROM occupancy_log WHERE spot_id = 1",
        ]:
            rows = db.session.execute(text(query)).fetchall()
            plan_text = " ".join(str(r) for r in rows).lower()
            assert "all" not in plan_text or "ref" in plan_text or "index" in plan_text, (
                f"Expected index scan for: {query}"
            )


# ===================================================================
#  F. Concurrent Access / Race Conditions
# ===================================================================


class TestConcurrentAccess:
    """Simulate multi-threaded DB access to test row-level locking.

    All tests require a real MySQL/MariaDB connection because:
      - In-memory SQLite shares a single connection across threads.
      - File-based SQLite uses process-level locks, not row-level.
      - Only MySQL/PostgreSQL support row-level locking (SELECT FOR UPDATE).
    """

    @requires_mysql
    def test_concurrent_last_spot_assignment(self, seeded_db):
        """Two threads race to occupy the last available spot.

        Exactly one must succeed; the other must get a conflict or find
        no available spot.
        """
        # Set floor to 1 available spot
        floor = db.session.get(Floor, seeded_db["floor_id"])
        floor.available_spots = 1
        db.session.commit()

        barrier = threading.Barrier(2, timeout=5)
        results = [None, None]

        def assign_spot(idx):
            with app.app_context():
                try:
                    barrier.wait()
                    spot = ParkingSpot.query.filter_by(
                        floor_id=seeded_db["floor_id"],
                        status=SpotStatusEnum.available,
                    ).first()
                    if spot:
                        spot.status = SpotStatusEnum.occupied
                        f = Floor.query.get(seeded_db["floor_id"])
                        f.available_spots -= 1
                        db.session.commit()
                        results[idx] = "success"
                    else:
                        results[idx] = "no_spot"
                except Exception as exc:
                    db.session.rollback()
                    results[idx] = f"error: {exc}"

        t1 = threading.Thread(target=assign_spot, args=(0,))
        t2 = threading.Thread(target=assign_spot, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        successes = results.count("success")
        assert successes <= 1, (
            f"Both threads succeeded — double-booking detected: {results}"
        )

    @requires_mysql
    def test_concurrent_ticket_close(self, seeded_db):
        """Two threads try to close the same ticket simultaneously.

        Either both succeed idempotently or one gets a conflict error.
        """
        barrier = threading.Barrier(2, timeout=5)
        results = [None, None]

        def close_ticket(idx):
            with app.app_context():
                try:
                    barrier.wait()
                    ticket = Ticket.query.get(seeded_db["ticket_id"])
                    if ticket and ticket.status == TicketStatusEnum.active:
                        ticket.status = TicketStatusEnum.closed
                        ticket.exit_timestamp = datetime.utcnow()
                        ticket.duration = 60
                        ticket.total_fee = Decimal("7.00")
                        db.session.commit()
                        results[idx] = "closed"
                    else:
                        results[idx] = "already_closed"
                except Exception as exc:
                    db.session.rollback()
                    results[idx] = f"error: {exc}"

        t1 = threading.Thread(target=close_ticket, args=(0,))
        t2 = threading.Thread(target=close_ticket, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        closed_count = results.count("closed")
        assert closed_count >= 1, f"Neither thread closed the ticket: {results}"

    @requires_mysql
    def test_floor_counter_no_negative(self, seeded_db):
        """Two threads each decrement available_spots on a floor with 1 spot left.

        The counter must not go below zero.
        """
        floor = db.session.get(Floor, seeded_db["floor_id"])
        floor.available_spots = 1
        db.session.commit()

        barrier = threading.Barrier(2, timeout=5)
        results = [None, None]

        def decrement(idx):
            with app.app_context():
                try:
                    barrier.wait()
                    f = Floor.query.get(seeded_db["floor_id"])
                    if f.available_spots > 0:
                        f.available_spots -= 1
                        db.session.commit()
                        results[idx] = "decremented"
                    else:
                        results[idx] = "skipped"
                except Exception as exc:
                    db.session.rollback()
                    results[idx] = f"error: {exc}"

        t1 = threading.Thread(target=decrement, args=(0,))
        t2 = threading.Thread(target=decrement, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        with app.app_context():
            floor = db.session.get(Floor, seeded_db["floor_id"])
            assert floor.available_spots >= 0, (
                f"available_spots went negative ({floor.available_spots}): {results}"
            )


# ===================================================================
#  G. Data Integrity Edge Cases
# ===================================================================


class TestDataIntegrityEdgeCases:
    """Test NULL handling, default values, and precision for columns
    not covered by the constraint tests above."""

    def test_vehicle_null_customer_id_accepted(self, db_session):
        """Walk-up vehicle with no customer account must be accepted.

        Vehicle.customer_id is nullable=True in models.py.
        """
        v = Vehicle(
            license_plate="WALKIN-01",
            vehicle_type=VehicleTypeEnum.car,
            customer_id=None,
        )
        db_session.add(v)
        db_session.flush()
        assert v.vehicle_id is not None
        assert v.customer_id is None

    def test_ticket_null_exit_fields_valid(self, seeded_db):
        """An active ticket has NULL exit_timestamp, exit_gate_id, duration,
        and total_fee — all nullable in models.py."""
        ticket = db.session.get(Ticket, seeded_db["ticket_id"])
        assert ticket.status == TicketStatusEnum.active
        assert ticket.exit_timestamp is None
        assert ticket.exit_gate_id is None
        assert ticket.duration is None
        assert ticket.total_fee is None

    def test_ticket_null_entry_gate_valid(self, seeded_db):
        """Ticket with entry_gate_id=None is valid (nullable=True in models.py).

        Note: schema.sq1 says NOT NULL, but models.py allows NULL.
        This documents the ORM behaviour.
        """
        v = Vehicle(license_plate="NOGATE-01", vehicle_type=VehicleTypeEnum.car)
        db.session.add(v)
        db.session.flush()
        t = Ticket(
            spot_id=seeded_db["spot_avail_id"],
            vehicle_id=v.vehicle_id,
            entry_gate_id=None,
            status=TicketStatusEnum.active,
        )
        db.session.add(t)
        db.session.flush()
        assert t.ticket_id is not None
        assert t.entry_gate_id is None

    def test_reservation_null_customer_and_vehicle(self, db_session):
        """Reservation with both customer_id and vehicle_id as NULL is valid.

        models.py: both are nullable=True.
        """
        r = Reservation(
            customer_id=None,
            vehicle_id=None,
            start_datetime=datetime.utcnow(),
            status=ReservationStatusEnum.confirmed,
        )
        db_session.add(r)
        db_session.flush()
        assert r.reservation_id is not None

    def test_system_event_null_staff_id(self, db_session):
        """SystemEvent with staff_id=None (system-generated) is valid."""
        se = SystemEvent(
            staff_id=None,
            source="test_module",
            description="Automated system event with no operator",
        )
        db_session.add(se)
        db_session.flush()
        assert se.event_id is not None
        assert se.staff_id is None

    def test_customer_email_not_null(self, db_session):
        """Customer with email=None must be rejected (nullable=False)."""
        db_session.add(
            Customer(email=None, account_status=AccountStatusEnum.active)
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_garage_name_not_null(self, db_session):
        """Garage with name=None must be rejected (nullable=False)."""
        db_session.add(Garage(name=None, total_capacity=10, number_of_floors=1))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_staff_password_hash_not_null(self, db_session):
        """Staff with password_hash=None must be rejected (nullable=False)."""
        db_session.add(
            Staff(
                name="Test",
                username="nopass",
                password_hash=None,
                role=StaffRoleEnum.admin,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_ticket_spot_id_not_null(self, seeded_db):
        """Ticket with spot_id=None must be rejected (nullable=False)."""
        db.session.add(
            Ticket(
                spot_id=None,
                vehicle_id=seeded_db["vehicle_id"],
                status=TicketStatusEnum.active,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_ticket_vehicle_id_not_null(self, seeded_db):
        """Ticket with vehicle_id=None must be rejected (nullable=False)."""
        db.session.add(
            Ticket(
                spot_id=seeded_db["spot_avail_id"],
                vehicle_id=None,
                status=TicketStatusEnum.active,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_spot_default_status_available(self, seeded_db):
        """ParkingSpot without explicit status defaults to 'available'."""
        spot = ParkingSpot(
            floor_id=seeded_db["floor_id"],
            spot_type=SpotTypeEnum.standard,
        )
        db.session.add(spot)
        db.session.flush()
        assert spot.status == SpotStatusEnum.available

    def test_decimal_precision_fee(self, seeded_db):
        """Numeric(8,2) stores Decimal values with correct precision."""
        v = Vehicle(license_plate="PREC-01", vehicle_type=VehicleTypeEnum.car)
        db.session.add(v)
        db.session.flush()
        t = Ticket(
            spot_id=seeded_db["spot_avail_id"],
            vehicle_id=v.vehicle_id,
            status=TicketStatusEnum.closed,
            total_fee=Decimal("123456.78"),
        )
        db.session.add(t)
        db.session.flush()

        refreshed = db.session.get(Ticket, t.ticket_id)
        assert refreshed.total_fee == Decimal("123456.78")
