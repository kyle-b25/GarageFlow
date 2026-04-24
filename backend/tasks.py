"""
tasks.py — GarageFlow Background Tasks

Scheduled tasks that run periodically via APScheduler.
"""
from datetime import datetime, timedelta


def expire_stale_reservations():
    """Mark confirmed reservations past their scheduled arrival as expired."""
    from app import app, db
    from models import Reservation, ReservationStatusEnum

    with app.app_context():
        now = datetime.utcnow()
        stale = Reservation.query.filter(
            Reservation.status == ReservationStatusEnum.confirmed,
            Reservation.start_datetime < now,
        ).all()

        for r in stale:
            r.status = ReservationStatusEnum.expired

        if stale:
            db.session.commit()


def cleanup_stale_login_attempts():
    """Delete LoginAttempt rows whose window has expired.

    Prevents unbounded table growth from distributed brute-force attempts.
    Runs every 10 minutes via APScheduler.
    """
    from app import app, db
    from models import LoginAttempt

    with app.app_context():
        # _RATE_LIMIT_WINDOW is 60s; delete anything older than 2 minutes
        # to give a safety margin beyond the active window.
        cutoff = datetime.utcnow() - timedelta(seconds=120)
        deleted = LoginAttempt.query.filter(
            LoginAttempt.window_start < cutoff,
        ).delete(synchronize_session=False)
        if deleted:
            db.session.commit()


def reconcile_floor_counters():
    """Recompute floor.available_spots from actual spot statuses.

    Corrects counter drift caused by crashes mid-transaction where
    spot status and floor counter diverge. Runs every 10 minutes.
    """
    from app import app, db
    from sqlalchemy import func
    from models import Floor, ParkingSpot, SpotStatusEnum

    with app.app_context():
        floors = Floor.query.all()
        corrected = 0
        for floor in floors:
            actual = ParkingSpot.query.filter_by(
                floor_id=floor.floor_id,
                status=SpotStatusEnum.available,
            ).count()
            if floor.available_spots != actual:
                floor.available_spots = actual
                corrected += 1
        if corrected:
            db.session.commit()


def flag_stale_pending_payments():
    """Flag payments stuck in 'pending' for over 1 hour.

    If a Stripe webhook never fires after ticket exit, the payment
    stays pending indefinitely. This task marks them as 'failed'
    so operators can investigate.
    """
    from app import app, db
    from models import Payment, PaymentStatusEnum

    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(hours=1)
        stale = Payment.query.filter(
            Payment.payment_status == PaymentStatusEnum.pending,
            Payment.payment_timestamp < cutoff,
        ).all()

        for payment in stale:
            payment.payment_status = PaymentStatusEnum.failed

        if stale:
            db.session.commit()
