"""
spaces_bp.py — Parking Spaces and Floors API Blueprint

CRUD endpoints for floors and parking spaces, plus the assign_space() helper.
"""
from flask import Blueprint, jsonify, request
from app import db
from models import Floor, ParkingSpot, SpotTypeEnum, SpotStatusEnum

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


# ── Assignment Helper ────────────────────────────────────────────────

DRIVER_CLASS_MAP = {
    "accessibility": SpotTypeEnum.accessibility,
    "employee": SpotTypeEnum.staff,
    "eco": SpotTypeEnum.standard,
    "standard": SpotTypeEnum.standard,
}


def assign_space(driver_class):
    """Return the best available ParkingSpot for the given driver_class, or None."""
    spot_type = DRIVER_CLASS_MAP.get(driver_class)
    if spot_type is None:
        return None
    return (
        ParkingSpot.query
        .join(Floor)
        .filter(
            ParkingSpot.status == SpotStatusEnum.available,
            ParkingSpot.spot_type == spot_type,
        )
        .order_by(Floor.floor_number.asc())
        .first()
    )


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
        floor = Floor(
            garage_id=garage_id,
            floor_number=floor_number,
            floor_name=data.get('floorName'),
            total_spots=total_spots,
            available_spots=0,
        )
        db.session.add(floor)
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
            floor.total_spots = data['totalSpots']
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
        db.session.delete(floor)
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
        floor.total_spots += 1
        floor.available_spots += 1
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
            spot.status = new_status
            if old_status == SpotStatusEnum.available and new_status != SpotStatusEnum.available:
                floor.available_spots -= 1
            elif old_status != SpotStatusEnum.available and new_status == SpotStatusEnum.available:
                floor.available_spots += 1
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
        if spot.status == SpotStatusEnum.available:
            floor.available_spots -= 1
        floor.total_spots -= 1
        db.session.delete(spot)
        db.session.commit()
        return jsonify({"message": "space deleted"}), 200
    except Exception:
        db.session.rollback()
        return jsonify({"error": "server_error"}), 500
