"""
routes/gates.py — Gate CRUD and operator override endpoints

Endpoints:
  GET    /v1/gates                  — List all gates
  POST   /v1/gates                  — Create a gate (admin)
  GET    /v1/gates/<id>             — Get single gate
  PUT    /v1/gates/<id>             — Update gate status (admin)
  DELETE /v1/gates/<id>             — Delete gate (admin, only if unused)
  POST   /v1/gates/<id>/override    — Manual open/close with audit (admin)
"""

from flask import Blueprint, request, jsonify

from utils import login_required, require_role, log_error

gates_bp = Blueprint('gates', __name__, url_prefix='/v1/gates')


def _gate_json(gate):
    return {
        'gateId': gate.gate_id,
        'garageId': gate.garage_id,
        'gateType': gate.gate_type.value,
        'status': gate.status.value,
    }


# ── GET /v1/gates ─────────────────────────────────────────────

@gates_bp.route('', methods=['GET'])
def list_gates():
    """List all gates. Optional query param: garage_id."""
    from app import db
    from models import GateEvent

    try:
        q = GateEvent.query
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            q = q.filter(GateEvent.garage_id == garage_id)
        gates = q.all()
        return jsonify([_gate_json(g) for g in gates]), 200
    except Exception as exc:
        db.session.rollback()
        log_error('gates.list', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to list gates'}), 500


# ── POST /v1/gates ────────────────────────────────────────────

@gates_bp.route('', methods=['POST'])
@require_role('admin')
def create_gate():
    from app import db
    from models import GateEvent, GateTypeEnum, GateStatusEnum, Garage

    data = request.get_json(silent=True) or {}
    garage_id = data.get('garageId')
    gate_type = data.get('gateType')

    if not garage_id or not gate_type:
        return jsonify({'error': 'missing_required_field', 'message': 'garageId and gateType are required'}), 400

    if gate_type not in ('entry', 'exit'):
        return jsonify({'error': 'invalid_gate_type', 'message': 'gateType must be entry or exit'}), 400

    garage = Garage.query.get(garage_id)
    if not garage:
        return jsonify({'error': 'garage_not_found', 'message': 'No garage found with that ID'}), 404

    existing = GateEvent.query.filter_by(garage_id=garage_id, gate_type=GateTypeEnum[gate_type]).first()
    if existing:
        return jsonify({'error': 'duplicate_gate', 'message': f'{gate_type} gate already exists for this garage'}), 409

    try:
        gate = GateEvent(
            garage_id=garage_id,
            gate_type=GateTypeEnum[gate_type],
            status=GateStatusEnum.closed,
        )
        db.session.add(gate)
        db.session.commit()
        return jsonify(_gate_json(gate)), 201
    except Exception as exc:
        db.session.rollback()
        log_error('gates.create', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create gate'}), 500


# ── GET /v1/gates/<id> ────────────────────────────────────────

@gates_bp.route('/<int:gate_id>', methods=['GET'])
def get_gate(gate_id):
    from models import GateEvent

    gate = GateEvent.query.get(gate_id)
    if not gate:
        return jsonify({'error': 'gate_not_found', 'message': 'No gate found with that ID'}), 404
    return jsonify(_gate_json(gate)), 200


# ── PUT /v1/gates/<id> ────────────────────────────────────────

@gates_bp.route('/<int:gate_id>', methods=['PUT'])
@require_role('admin')
def update_gate(gate_id):
    from app import db
    from models import GateEvent, GateStatusEnum

    gate = GateEvent.query.get(gate_id)
    if not gate:
        return jsonify({'error': 'gate_not_found', 'message': 'No gate found with that ID'}), 404

    data = request.get_json(silent=True) or {}
    new_status = data.get('status')

    if not new_status:
        return jsonify({'error': 'missing_required_field', 'message': 'status is required'}), 400

    valid_statuses = {s.value for s in GateStatusEnum}
    if new_status not in valid_statuses:
        return jsonify({'error': 'invalid_status', 'message': f'status must be one of: {", ".join(sorted(valid_statuses))}'}), 400

    try:
        gate.status = GateStatusEnum(new_status)
        db.session.commit()
        return jsonify(_gate_json(gate)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('gates.update', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to update gate'}), 500


# ── DELETE /v1/gates/<id> ─────────────────────────────────────

@gates_bp.route('/<int:gate_id>', methods=['DELETE'])
@require_role('admin')
def delete_gate(gate_id):
    from app import db
    from models import GateEvent, Ticket

    gate = GateEvent.query.get(gate_id)
    if not gate:
        return jsonify({'error': 'gate_not_found', 'message': 'No gate found with that ID'}), 404

    # Block deletion if any tickets reference this gate
    used = Ticket.query.filter(
        (Ticket.entry_gate_id == gate_id) | (Ticket.exit_gate_id == gate_id)
    ).first()
    if used:
        return jsonify({'error': 'gate_in_use', 'message': 'Cannot delete gate referenced by tickets'}), 409

    try:
        db.session.delete(gate)
        db.session.commit()
        return '', 204
    except Exception as exc:
        db.session.rollback()
        log_error('gates.delete', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to delete gate'}), 500


# ── POST /v1/gates/<id>/override — SR-12 ─────────────────────

@gates_bp.route('/<int:gate_id>/override', methods=['POST'])
@require_role('admin')
def override_gate(gate_id):
    """Manual gate open/close with audit trail."""
    from app import db
    from flask import g
    from models import GateEvent, GateStatusEnum, SystemEvent

    gate = GateEvent.query.get(gate_id)
    if not gate:
        return jsonify({'error': 'gate_not_found', 'message': 'No gate found with that ID'}), 404

    data = request.get_json(silent=True) or {}
    action = data.get('action')
    reason = data.get('reason', '')

    if action not in ('open', 'close', 'out_of_order'):
        return jsonify({'error': 'invalid_action', 'message': 'action must be open, close, or out_of_order'}), 400

    old_status = gate.status.value

    try:
        _ACTION_TO_STATUS = {'open': GateStatusEnum.open, 'close': GateStatusEnum.closed, 'out_of_order': GateStatusEnum.out_of_order}
        gate.status = _ACTION_TO_STATUS[action]
        db.session.add(SystemEvent(
            staff_id=g.current_user.operator_id,
            source='gate_override',
            description=f'Gate {gate_id} ({gate.gate_type.value}): {old_status} -> {action}. Reason: {reason}',
        ))
        db.session.commit()
        return jsonify(_gate_json(gate)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('gates.override', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to override gate'}), 500
