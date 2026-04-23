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
