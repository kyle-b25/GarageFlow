"""
services/occupancy.py — Shared occupancy validation for GarageFlow.

Centralises the four-step spot assignment check used by both ticket
creation and reservation scheduling.  No Flask routes live here — this
is a pure service module.
"""

from datetime import timedelta


# ------------------------------------------------------------------
#  Custom exceptions
# ------------------------------------------------------------------

class GarageFullError(Exception):
    """No available spots of any type remain in the garage."""
    http_status = 409
    error_key = "garage_full"


class NoSpotAvailableError(Exception):
    """No available spot of the requested type exists on any floor."""
    http_status = 409
    error_key = "no_spot_available"


class ReservationConflictError(Exception):
    """Every candidate spot conflicts with a confirmed reservation."""
    http_status = 409
    error_key = "reservation_conflict"


# ------------------------------------------------------------------
#  Driver-class → spot-type mapping (mirrors CLAUDE.md spec)
# ------------------------------------------------------------------

_DRIVER_CLASS_TO_SPOT_TYPE = {
    "standard": "standard",
    "accessibility": "accessibility",
    "employee": "staff",
    "eco": "staff",
}

_SPOT_TYPE_TO_DRIVER_CLASSES = {}
for _dc, _st in _DRIVER_CLASS_TO_SPOT_TYPE.items():
    _SPOT_TYPE_TO_DRIVER_CLASSES.setdefault(_st, set()).add(_dc)


# ------------------------------------------------------------------
#  Public API
# ------------------------------------------------------------------

def validate_and_assign_spot(garage_id, spot_type, arrival_datetime=None):
    """Select an available parking spot after four sequential checks.

    Args:
        garage_id: Primary key of the target ``Garage``.
        spot_type: A ``SpotTypeEnum`` member (e.g. ``SpotTypeEnum.standard``).
        arrival_datetime: Optional ``datetime``.  When provided, candidate
            spots are checked against confirmed reservations within a
            ±30-minute window and conflicting spots are skipped.

    Returns:
        The chosen ``ParkingSpot`` ORM instance.  The caller is
        responsible for setting its status to *occupied* and committing.

    Raises:
        GarageFullError: The garage has zero available spots of any type.
        NoSpotAvailableError: No available spot of *spot_type* exists.
        ReservationConflictError: Every candidate spot of *spot_type*
            conflicts with a confirmed reservation in the arrival window.
    """
    from app import db
    from models import (
        Floor, ParkingSpot, Reservation,
        SpotStatusEnum, SpotTypeEnum, ReservationStatusEnum,
    )

    # ---- Step 1: total garage capacity ----
    available_count = (
        db.session.query(db.func.count(ParkingSpot.spot_id))
        .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
        .filter(
            Floor.garage_id == garage_id,
            ParkingSpot.status == SpotStatusEnum.available,
        )
        .scalar()
    )
    if available_count == 0:
        raise GarageFullError("Garage is full — no available spots")

    # ---- Step 2: per-floor availability for requested type ----
    floor_counts = (
        db.session.query(
            Floor.floor_id,
            db.func.count(ParkingSpot.spot_id).label("cnt"),
        )
        .join(ParkingSpot, ParkingSpot.floor_id == Floor.floor_id)
        .filter(
            Floor.garage_id == garage_id,
            ParkingSpot.status == SpotStatusEnum.available,
            ParkingSpot.spot_type == spot_type,
        )
        .group_by(Floor.floor_id)
        .order_by(db.desc("cnt"))
        .all()
    )
    if not floor_counts:
        raise NoSpotAvailableError(
            f"No available {spot_type.value} spot in garage {garage_id}"
        )

    # ---- Steps 3 & 4: spot selection with optional conflict check ----
    # Build ordered list of candidate floor IDs (most available first).
    candidate_floor_ids = [fid for fid, _ in floor_counts]

    # Pre-compute the set of driver_class values that map to the
    # requested spot_type so we can match against Reservation.driver_class.
    matching_driver_classes = _SPOT_TYPE_TO_DRIVER_CLASSES.get(
        spot_type.value, set()
    )

    for floor_id in candidate_floor_ids:
        # Step 3 — exact type match on this floor
        candidates = (
            ParkingSpot.query
            .filter_by(
                floor_id=floor_id,
                spot_type=spot_type,
                status=SpotStatusEnum.available,
            )
            .all()
        )

        if not arrival_datetime or not matching_driver_classes:
            # No conflict check needed — return first candidate
            if candidates:
                return candidates[0]
            continue

        # Step 4 — reservation conflict filtering
        window_start = arrival_datetime - timedelta(minutes=30)
        window_end = arrival_datetime + timedelta(minutes=30)

        # Get the floor_number for reservation matching
        floor = Floor.query.get(floor_id)

        conflicting_count = (
            db.session.query(db.func.count(Reservation.reservation_id))
            .filter(
                Reservation.status == ReservationStatusEnum.confirmed,
                Reservation.floor_number == floor.floor_number,
                Reservation.driver_class.in_(matching_driver_classes),
                Reservation.start_datetime >= window_start,
                Reservation.start_datetime <= window_end,
            )
            .scalar()
        )

        # If confirmed reservations on this floor for this type
        # outnumber (or equal) the available candidates, skip the floor.
        conflict_free = len(candidates) - conflicting_count
        if conflict_free > 0:
            return candidates[0]

    # Exhausted all floors
    if arrival_datetime and matching_driver_classes:
        raise ReservationConflictError(
            f"All {spot_type.value} spots conflict with confirmed reservations"
        )
    raise NoSpotAvailableError(
        f"No available {spot_type.value} spot in garage {garage_id}"
    )
