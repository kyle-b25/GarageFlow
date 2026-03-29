"""
tasks.py — GarageFlow Background Tasks

Scheduled tasks that run periodically via APScheduler.
"""
from datetime import datetime


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
