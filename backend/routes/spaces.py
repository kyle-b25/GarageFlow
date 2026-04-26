"""
spaces_bp.py — Parking Spaces and Floors API Blueprint

CRUD endpoints for floors and parking spaces, plus the assign_space() helper.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request
from app import db
from models import (
    Garage, Floor, ParkingSpot, Ticket,
    SpotTypeEnum, SpotStatusEnum, TicketStatusEnum,
    OccupancyLog, OccupancyChangeEnum,
)
from utils import log_error, require_role

spaces_bp = Blueprint('spaces', __name__, url_prefix='/v1')

# ── Serializers ──────────────────────────────────────────────────────

def _space_json(spot):
    return {
        "spaceId": spot.spot_id,
        "floorId": spot.floor_id,
        "floorNumber": spot.floor.floor_number,
        "type": spot.spot_type.value,
        "status": spot.status.value,
        "locationReference": spot.location_reference,
    }


def _floor_json(floor):
    return {
        "floorId": floor.floor_id,
        "floorNumber": floor.floor_number,
        "floorName": floor.floor_name,
        "totalSpots": floor.total_spots,
        "availableSpots": floor.available_spots,
    }


# ── Helpers ──────────────────────────────────────────────────────────

def _sync_garage(garage):
    """Recalculate garage total_capacity and number_of_floors from floor records."""
    floors = Floor.query.filter_by(garage_id=garage.garage_id).all()
    garage.number_of_floors = len(floors)
    garage.total_capacity = sum(f.total_spots for f in floors)


# ── Space Endpoints ──────────────────────────────────────────────────

@spaces_bp.route('/spaces', methods=['GET'])
def list_spaces():
    """List all parking spaces. Optional query param: garage_id."""
    try:
        q = ParkingSpot.query
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            q = q.join(Floor, ParkingSpot.floor_id == Floor.floor_id).filter(Floor.garage_id == garage_id)
        spots = q.all()
        return jsonify([_space_json(s) for s in spots]), 200
    except Exception as exc:
        log_error('spaces.list_spaces', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to list spaces"}), 500


@spaces_bp.route('/spaces/available', methods=['GET'])
def list_available_spaces():
    """List available parking spaces, optionally filtered by spot type and garage_id."""
    try:
        q = ParkingSpot.query.filter(ParkingSpot.status == SpotStatusEnum.available)
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            q = q.join(Floor, ParkingSpot.floor_id == Floor.floor_id).filter(Floor.garage_id == garage_id)
        type_param = request.args.get('type')
        if type_param:
            try:
                spot_type = SpotTypeEnum(type_param)
            except ValueError:
                return jsonify({"error": "invalid_spot_type", "message": "Unrecognized spot type value"}), 400
            q = q.filter(ParkingSpot.spot_type == spot_type)
        spots = q.all()
        return jsonify([_space_json(s) for s in spots]), 200
    except Exception as exc:
        log_error('spaces.list_available_spaces', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to list available spaces"}), 500


# ── Floor Endpoints ──────────────────────────────────────────────────

@spaces_bp.route('/floors', methods=['GET'])
def list_floors():
    """List all floors. Optional query param: garage_id."""
    try:
        q = Floor.query
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            q = q.filter(Floor.garage_id == garage_id)
        floors = q.all()
        return jsonify([_floor_json(f) for f in floors]), 200
    except Exception as exc:
        log_error('spaces.list_floors', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to list floors"}), 500


@spaces_bp.route('/floors', methods=['POST'])
@require_role('admin')
def create_floor():
    """Create a new floor for a garage."""
    try:
        data = request.get_json(silent=True) or {}
        garage_id = data.get('garageId')
        floor_number = data.get('floorNumber')
        total_spots = data.get('totalSpots')
        spots_config = data.get('spots')  # e.g. {"standard": 5, "accessibility": 2}

        if garage_id is None or floor_number is None:
            return jsonify({"error": "missing_required_field", "message": "garageId and floorNumber are required"}), 400
        if total_spots is None and not spots_config:
            return jsonify({"error": "missing_required_field", "message": "totalSpots or spots breakdown is required"}), 400

        garage = Garage.query.get(garage_id)
        if not garage:
            return jsonify({"error": "garage_not_found", "message": "No garage found with that ID"}), 404

        # Validate spot types upfront
        if spots_config:
            for type_name in spots_config:
                try:
                    SpotTypeEnum(type_name)
                except ValueError:
                    return jsonify({"error": "invalid_spot_type", "message": f"Unknown spot type: {type_name}"}), 400

        # Calculate total from breakdown if provided
        if spots_config:
            computed_total = sum(int(v) for v in spots_config.values())
        else:
            computed_total = total_spots

        floor = Floor(
            garage_id=garage_id,
            floor_number=floor_number,
            floor_name=data.get('floorName'),
            total_spots=computed_total,
            available_spots=computed_total if spots_config else 0,
        )
        db.session.add(floor)
        db.session.flush()

        # Auto-create spots from breakdown
        if spots_config:
            idx = 1
            for type_name, count in spots_config.items():
                spot_type = SpotTypeEnum(type_name)
                for _ in range(int(count)):
                    db.session.add(ParkingSpot(
                        floor_id=floor.floor_id,
                        spot_type=spot_type,
                        status=SpotStatusEnum.available,
                        location_reference=f'{floor_number}-{idx}',
                    ))
                    idx += 1

        _sync_garage(garage)
        db.session.commit()
        return jsonify(_floor_json(floor)), 201
    except Exception as exc:
        db.session.rollback()
        log_error('spaces.create_floor', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to create floor"}), 500


@spaces_bp.route('/floors/<int:floor_id>', methods=['GET'])
def get_floor(floor_id):
    """Return details of a single floor."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        return jsonify(_floor_json(floor)), 200
    except Exception as exc:
        log_error('spaces.get_floor', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to fetch floor"}), 500


@spaces_bp.route('/floors/<int:floor_id>', methods=['PUT'])
@require_role('admin')
def update_floor(floor_id):
    """Update a floor's name, number, total spots, or spot type breakdown."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        data = request.get_json(silent=True) or {}
        if 'floorName' in data:
            floor.floor_name = data['floorName']
        if 'floorNumber' in data:
            floor.floor_number = data['floorNumber']

        spots_config = data.get('spots')
        if spots_config:
            # Validate spot types
            for type_name in spots_config:
                try:
                    SpotTypeEnum(type_name)
                except ValueError:
                    return jsonify({"error": "invalid_spot_type", "message": f"Unknown spot type: {type_name}"}), 400

            # Adjust each spot type
            for type_name, desired in spots_config.items():
                spot_type = SpotTypeEnum(type_name)
                desired = int(desired)
                current_all = ParkingSpot.query.filter_by(floor_id=floor_id, spot_type=spot_type).all()
                current_count = len(current_all)

                if desired > current_count:
                    # Add new spots
                    max_ref = ParkingSpot.query.filter_by(floor_id=floor_id).count()
                    for i in range(desired - current_count):
                        max_ref += 1
                        db.session.add(ParkingSpot(
                            floor_id=floor_id,
                            spot_type=spot_type,
                            status=SpotStatusEnum.available,
                            location_reference=f'{floor.floor_number}-{max_ref}',
                        ))
                elif desired < current_count:
                    # Remove available spots of this type
                    to_remove = current_count - desired
                    available_of_type = [s for s in current_all if s.status == SpotStatusEnum.available]
                    if len(available_of_type) < to_remove:
                        return jsonify({
                            "error": "cannot_reduce_spots",
                            "message": f"Cannot remove {to_remove} {type_name} spots — only {len(available_of_type)} are unoccupied",
                        }), 400
                    for s in available_of_type[:to_remove]:
                        db.session.delete(s)

            # Recalculate floor totals from actual spot records
            db.session.flush()
            total = ParkingSpot.query.filter_by(floor_id=floor_id).count()
            available = ParkingSpot.query.filter_by(floor_id=floor_id, status=SpotStatusEnum.available).count()
            floor.total_spots = total
            floor.available_spots = available
            garage = Garage.query.get(floor.garage_id)
            if garage:
                _sync_garage(garage)

        elif 'totalSpots' in data:
            new_total = data['totalSpots']
            occupied_count = ParkingSpot.query.filter_by(
                floor_id=floor_id, status=SpotStatusEnum.occupied
            ).count()
            if new_total < occupied_count:
                return jsonify({"error": "invalid_total_spots",
                                "message": "totalSpots cannot be less than occupied spots"}), 400
            actual_available = ParkingSpot.query.filter_by(
                floor_id=floor_id, status=SpotStatusEnum.available
            ).count()
            floor.total_spots = new_total
            floor.available_spots = min(actual_available, new_total - occupied_count)
            garage = Garage.query.get(floor.garage_id)
            if garage:
                _sync_garage(garage)

        db.session.commit()
        return jsonify(_floor_json(floor)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('spaces.update_floor', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to update floor"}), 500


@spaces_bp.route('/floors/<int:floor_id>', methods=['DELETE'])
@require_role('admin')
def delete_floor(floor_id):
    """Delete a floor if it has no occupied spots or active tickets."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        # Block if any spot is occupied or has active tickets
        has_occupied = ParkingSpot.query.filter_by(
            floor_id=floor_id, status=SpotStatusEnum.occupied
        ).first()
        has_active_tickets = (
            Ticket.query.join(ParkingSpot)
            .filter(ParkingSpot.floor_id == floor_id,
                    Ticket.status == TicketStatusEnum.active)
            .first()
        )
        if has_occupied or has_active_tickets:
            return jsonify({"error": "floor_has_active_usage", "message": "Floor has occupied spots or active tickets"}), 400
        garage = Garage.query.get(floor.garage_id)
        db.session.delete(floor)
        db.session.flush()
        if garage:
            _sync_garage(garage)
        db.session.commit()
        return jsonify({"message": "floor deleted"}), 200
    except Exception as exc:
        db.session.rollback()
        log_error('spaces.delete_floor', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to delete floor"}), 500


# ── Floor → Space Endpoints ──────────────────────────────────────────

@spaces_bp.route('/floors/<int:floor_id>/spaces', methods=['GET'])
def list_floor_spaces(floor_id):
    """List all spaces on a floor, optionally filtered by status."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        q = ParkingSpot.query.filter(ParkingSpot.floor_id == floor_id)
        status_param = request.args.get('status')
        if status_param:
            try:
                status_enum = SpotStatusEnum(status_param)
            except ValueError:
                return jsonify({"error": "invalid_status", "message": "Unrecognized spot status value"}), 400
            q = q.filter(ParkingSpot.status == status_enum)
        spots = q.all()
        return jsonify([_space_json(s) for s in spots]), 200
    except Exception as exc:
        log_error('spaces.list_floor_spaces', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to list floor spaces"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces', methods=['POST'])
@require_role('admin')
def create_space(floor_id):
    """Create a new parking space on a floor."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        data = request.get_json(silent=True) or {}
        type_val = data.get('type')
        if type_val is None:
            return jsonify({"error": "missing_required_field", "message": "type is required"}), 400
        try:
            spot_type = SpotTypeEnum(type_val)
        except ValueError:
            return jsonify({"error": "invalid_spot_type", "message": "Unrecognized spot type value"}), 400
        spot = ParkingSpot(
            floor_id=floor_id,
            spot_type=spot_type,
            status=SpotStatusEnum.available,
            location_reference=data.get('locationReference'),
        )
        db.session.add(spot)
        db.session.flush()
        # Recalculate total_spots from actual count
        floor.total_spots = ParkingSpot.query.filter_by(floor_id=floor_id).count()
        floor.available_spots += 1
        garage = Garage.query.get(floor.garage_id)
        if garage:
            _sync_garage(garage)
        db.session.commit()
        return jsonify(_space_json(spot)), 201
    except Exception as exc:
        db.session.rollback()
        log_error('spaces.create_space', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to create space"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces/<int:space_id>', methods=['GET'])
def get_space(floor_id, space_id):
    """Return details of a single parking space."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        spot = ParkingSpot.query.get(space_id)
        if not spot or spot.floor_id != floor_id:
            return jsonify({"error": "space_not_found", "message": "No space found with that ID on this floor"}), 404
        return jsonify(_space_json(spot)), 200
    except Exception as exc:
        log_error('spaces.get_space', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to fetch space"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces/<int:space_id>', methods=['PUT'])
@require_role('admin')
def update_space(floor_id, space_id):
    """Update a parking space's type, location reference, or status."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        spot = ParkingSpot.query.get(space_id)
        if not spot or spot.floor_id != floor_id:
            return jsonify({"error": "space_not_found", "message": "No space found with that ID on this floor"}), 404
        data = request.get_json(silent=True) or {}
        if 'type' in data:
            try:
                spot.spot_type = SpotTypeEnum(data['type'])
            except ValueError:
                return jsonify({"error": "invalid_spot_type", "message": "Unrecognized spot type value"}), 400
        if 'locationReference' in data:
            spot.location_reference = data['locationReference']
        if 'status' in data:
            try:
                new_status = SpotStatusEnum(data['status'])
            except ValueError:
                return jsonify({"error": "invalid_status", "message": "Unrecognized spot status value"}), 400
            old_status = spot.status
            if old_status != new_status:
                spot.status = new_status
                if old_status == SpotStatusEnum.available and new_status != SpotStatusEnum.available:
                    floor.available_spots -= 1
                elif old_status != SpotStatusEnum.available and new_status == SpotStatusEnum.available:
                    floor.available_spots += 1
                # Log occupancy change
                if new_status == SpotStatusEnum.occupied:
                    change = OccupancyChangeEnum.occupied
                elif new_status == SpotStatusEnum.available:
                    change = OccupancyChangeEnum.freed
                else:
                    change = OccupancyChangeEnum.freed  # out_of_order treated as freed
                db.session.add(OccupancyLog(
                    spot_id=spot.spot_id,
                    changed_at=datetime.utcnow(),
                    change_type=change,
                ))
        db.session.commit()
        return jsonify(_space_json(spot)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('spaces.update_space', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to update space"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces/<int:space_id>', methods=['DELETE'])
@require_role('admin')
def delete_space(floor_id, space_id):
    """Delete a parking space if it is not occupied and has no active tickets."""
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found", "message": "No floor found with that ID"}), 404
        spot = ParkingSpot.query.get(space_id)
        if not spot or spot.floor_id != floor_id:
            return jsonify({"error": "space_not_found", "message": "No space found with that ID on this floor"}), 404
        # Block if spot is occupied or has active tickets
        if spot.status == SpotStatusEnum.occupied:
            return jsonify({"error": "spot_has_active_usage", "message": "Spot is currently occupied"}), 400
        has_active_tickets = Ticket.query.filter_by(
            spot_id=space_id, status=TicketStatusEnum.active
        ).first()
        if has_active_tickets:
            return jsonify({"error": "spot_has_active_usage", "message": "Spot has active tickets"}), 400
        if spot.status == SpotStatusEnum.available:
            floor.available_spots -= 1
        db.session.delete(spot)
        db.session.flush()
        # Recalculate total_spots from actual count
        floor.total_spots = ParkingSpot.query.filter_by(floor_id=floor_id).count()
        garage = Garage.query.get(floor.garage_id)
        if garage:
            _sync_garage(garage)
        db.session.commit()
        return jsonify({"message": "space deleted"}), 200
    except Exception as exc:
        db.session.rollback()
        log_error('spaces.delete_space', str(exc))
        return jsonify({"error": "server_error", "message": "Failed to delete space"}), 500
