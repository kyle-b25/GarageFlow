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
    try:
        spots = ParkingSpot.query.all()
        return jsonify([_space_json(s) for s in spots]), 200
    except Exception:
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/spaces/available', methods=['GET'])
def list_available_spaces():
    try:
        q = ParkingSpot.query.filter(ParkingSpot.status == SpotStatusEnum.available)
        type_param = request.args.get('type')
        if type_param:
            try:
                spot_type = SpotTypeEnum(type_param)
            except ValueError:
                return jsonify({"error": "invalid_spot_type"}), 400
            q = q.filter(ParkingSpot.spot_type == spot_type)
        spots = q.all()
        return jsonify([_space_json(s) for s in spots]), 200
    except Exception:
        return jsonify({"error": "server_error"}), 500


# ── Floor Endpoints ──────────────────────────────────────────────────

@spaces_bp.route('/floors', methods=['GET'])
def list_floors():
    try:
        floors = Floor.query.all()
        return jsonify([_floor_json(f) for f in floors]), 200
    except Exception:
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors', methods=['POST'])
def create_floor():
    try:
        data = request.get_json(silent=True) or {}
        garage_id = data.get('garageId')
        floor_number = data.get('floorNumber')
        total_spots = data.get('totalSpots')
        if garage_id is None or floor_number is None or total_spots is None:
            return jsonify({"error": "missing_required_field"}), 400
        garage = Garage.query.get(garage_id)
        if not garage:
            return jsonify({"error": "garage_not_found"}), 404
        floor = Floor(
            garage_id=garage_id,
            floor_number=floor_number,
            floor_name=data.get('floorName'),
            total_spots=total_spots,
            available_spots=0,
        )
        db.session.add(floor)
        db.session.flush()
        _sync_garage(garage)
        db.session.commit()
        return jsonify(_floor_json(floor)), 201
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>', methods=['GET'])
def get_floor(floor_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        return jsonify(_floor_json(floor)), 200
    except Exception:
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>', methods=['PUT'])
def update_floor(floor_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        data = request.get_json(silent=True) or {}
        if 'floorName' in data:
            floor.floor_name = data['floorName']
        if 'floorNumber' in data:
            floor.floor_number = data['floorNumber']
        if 'totalSpots' in data:
            new_total = data['totalSpots']
            occupied_count = ParkingSpot.query.filter_by(
                floor_id=floor_id, status=SpotStatusEnum.occupied
            ).count()
            if new_total < occupied_count:
                return jsonify({"error": "invalid_total_spots",
                                "message": "totalSpots cannot be less than occupied spots"}), 400
            floor.total_spots = new_total
            garage = Garage.query.get(floor.garage_id)
            if garage:
                _sync_garage(garage)
        db.session.commit()
        return jsonify(_floor_json(floor)), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>', methods=['DELETE'])
def delete_floor(floor_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
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
            return jsonify({"error": "floor_has_active_usage"}), 400
        garage = Garage.query.get(floor.garage_id)
        db.session.delete(floor)
        db.session.flush()
        if garage:
            _sync_garage(garage)
        db.session.commit()
        return jsonify({"message": "floor deleted"}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500


# ── Floor → Space Endpoints ──────────────────────────────────────────

@spaces_bp.route('/floors/<int:floor_id>/spaces', methods=['GET'])
def list_floor_spaces(floor_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        q = ParkingSpot.query.filter(ParkingSpot.floor_id == floor_id)
        status_param = request.args.get('status')
        if status_param:
            try:
                status_enum = SpotStatusEnum(status_param)
            except ValueError:
                return jsonify({"error": "invalid_status"}), 400
            q = q.filter(ParkingSpot.status == status_enum)
        spots = q.all()
        return jsonify([_space_json(s) for s in spots]), 200
    except Exception:
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces', methods=['POST'])
def create_space(floor_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        data = request.get_json(silent=True) or {}
        type_val = data.get('type')
        if type_val is None:
            return jsonify({"error": "missing_required_field"}), 400
        try:
            spot_type = SpotTypeEnum(type_val)
        except ValueError:
            return jsonify({"error": "invalid_spot_type"}), 400
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
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces/<int:space_id>', methods=['GET'])
def get_space(floor_id, space_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        spot = ParkingSpot.query.get(space_id)
        if not spot or spot.floor_id != floor_id:
            return jsonify({"error": "space_not_found"}), 404
        return jsonify(_space_json(spot)), 200
    except Exception:
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces/<int:space_id>', methods=['PUT'])
def update_space(floor_id, space_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        spot = ParkingSpot.query.get(space_id)
        if not spot or spot.floor_id != floor_id:
            return jsonify({"error": "space_not_found"}), 404
        data = request.get_json(silent=True) or {}
        if 'type' in data:
            try:
                spot.spot_type = SpotTypeEnum(data['type'])
            except ValueError:
                return jsonify({"error": "invalid_spot_type"}), 400
        if 'locationReference' in data:
            spot.location_reference = data['locationReference']
        if 'status' in data:
            try:
                new_status = SpotStatusEnum(data['status'])
            except ValueError:
                return jsonify({"error": "invalid_status"}), 400
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
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500


@spaces_bp.route('/floors/<int:floor_id>/spaces/<int:space_id>', methods=['DELETE'])
def delete_space(floor_id, space_id):
    try:
        floor = Floor.query.get(floor_id)
        if not floor:
            return jsonify({"error": "floor_not_found"}), 404
        spot = ParkingSpot.query.get(space_id)
        if not spot or spot.floor_id != floor_id:
            return jsonify({"error": "space_not_found"}), 404
        # Block if spot is occupied or has active tickets
        if spot.status == SpotStatusEnum.occupied:
            return jsonify({"error": "spot_has_active_usage"}), 400
        has_active_tickets = Ticket.query.filter_by(
            spot_id=space_id, status=TicketStatusEnum.active
        ).first()
        if has_active_tickets:
            return jsonify({"error": "spot_has_active_usage"}), 400
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
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500
