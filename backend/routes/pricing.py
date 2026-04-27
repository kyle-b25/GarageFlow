"""
routes/pricing.py — Pricing Rule CRUD + Base Rate (admin-only)

ALL endpoints require admin authentication.

Endpoints:
  GET    /v1/pricing/base-rate      — Return current base rate
  PUT    /v1/pricing/base-rate      — Update base rate
  GET    /v1/pricing                — List all custom rules (ordered by sort_order)
  POST   /v1/pricing                — Create custom rule
  GET    /v1/pricing/<id>           — Get single rule
  PUT    /v1/pricing/<id>           — Update rule
  DELETE /v1/pricing/<id>           — Delete rule
  POST   /v1/pricing/<id>/move      — Move rule up or down in priority order
"""

from decimal import Decimal, InvalidOperation

from flask import Blueprint, request, jsonify

from utils import require_role, log_error

pricing_bp = Blueprint('pricing', __name__, url_prefix='/v1/pricing')


# ------------------------------------------------------------------
#  Serialiser
# ------------------------------------------------------------------

def _rule_json(rule):
    return {
        'rateId':          rule.rate_id,
        'rateName':        rule.rate_name,
        'applicableHours': rule.applicable_hours,
        'ratePerHour':     float(rule.rate_per_hour),
        'description':     rule.description,
        'sortOrder':       rule.sort_order,
    }


def _parse_rate(value, field='ratePerHour'):
    """Parse a monetary rate. Returns (Decimal, None) or (None, error_str)."""
    try:
        d = Decimal(str(value))
        if d < 0:
            return None, f'{field} must be >= 0'
        return d, None
    except (InvalidOperation, TypeError, ValueError):
        return None, f'{field} must be a valid number'


def _ordered_rules():
    """All rules sorted by sort_order ASC, rate_id ASC as tiebreaker."""
    from models import PricingRule
    return PricingRule.query.order_by(PricingRule.sort_order, PricingRule.rate_id).all()


# ------------------------------------------------------------------
#  GET /v1/pricing/base-rate
# ------------------------------------------------------------------

@pricing_bp.route('/base-rate', methods=['GET'])
@require_role('admin')
def get_base_rate():
    from models import Garage
    try:
        garage = Garage.query.first()
        rate = float(garage.base_rate_per_hour) if garage else 2.00
        return jsonify({'baseRatePerHour': rate}), 200
    except Exception as exc:
        log_error('pricing.get_base_rate', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ------------------------------------------------------------------
#  PUT /v1/pricing/base-rate
# ------------------------------------------------------------------

@pricing_bp.route('/base-rate', methods=['PUT'])
@require_role('admin')
def update_base_rate():
    from app import db
    from models import Garage

    data = request.get_json(silent=True) or {}
    if 'baseRatePerHour' not in data:
        return jsonify({'error': 'missing_required_field',
                        'message': 'baseRatePerHour is required'}), 400

    rate, err = _parse_rate(data['baseRatePerHour'])
    if err:
        return jsonify({'error': 'invalid_rate', 'message': err}), 400

    try:
        garage = Garage.query.first()
        if not garage:
            return jsonify({'error': 'no_garage',
                            'message': 'No garage configured yet'}), 404
        garage.base_rate_per_hour = rate
        db.session.commit()
        return jsonify({'baseRatePerHour': float(garage.base_rate_per_hour)}), 200
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.update_base_rate', str(exc))
        return jsonify({'error': 'server_error',
                        'message': 'Failed to update base rate'}), 500


# ------------------------------------------------------------------
#  GET /v1/pricing
# ------------------------------------------------------------------

@pricing_bp.route('', methods=['GET'])
@require_role('admin')
def list_rules():
    try:
        rules = _ordered_rules()
        return jsonify([_rule_json(r) for r in rules]), 200
    except Exception as exc:
        log_error('pricing.list', str(exc))
        return jsonify({'error': 'server_error'}), 500


# ------------------------------------------------------------------
#  POST /v1/pricing
# ------------------------------------------------------------------

@pricing_bp.route('', methods=['POST'])
@require_role('admin')
def create_rule():
    from app import db
    from models import PricingRule

    data = request.get_json(silent=True) or {}

    for field in ('rateName', 'applicableHours', 'ratePerHour', 'description'):
        if data.get(field) is None or str(data.get(field, '')).strip() == '':
            return jsonify({'error': 'missing_required_field',
                            'message': f'{field} is required'}), 400

    rate, err = _parse_rate(data['ratePerHour'])
    if err:
        return jsonify({'error': 'invalid_rate', 'message': err}), 400

    try:
        # Place new rule at the end of the list
        max_order = db.session.query(db.func.max(PricingRule.sort_order)).scalar() or 0
        rule = PricingRule(
            rate_name=data['rateName'].strip(),
            applicable_hours=data['applicableHours'].strip(),
            rate_per_hour=rate,
            description=data['description'].strip(),
            sort_order=max_order + 1,
        )
        db.session.add(rule)
        db.session.commit()
        return jsonify(_rule_json(rule)), 201
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.create', str(exc))
        return jsonify({'error': 'server_error',
                        'message': 'Failed to create pricing rule'}), 500


# ------------------------------------------------------------------
#  GET /v1/pricing/<id>
# ------------------------------------------------------------------

@pricing_bp.route('/<int:rate_id>', methods=['GET'])
@require_role('admin')
def get_rule(rate_id):
    from models import PricingRule
    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found',
                        'message': 'No pricing rule found with that ID'}), 404
    return jsonify(_rule_json(rule)), 200


# ------------------------------------------------------------------
#  PUT /v1/pricing/<id>
# ------------------------------------------------------------------

@pricing_bp.route('/<int:rate_id>', methods=['PUT'])
@require_role('admin')
def update_rule(rate_id):
    from app import db
    from models import PricingRule

    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found',
                        'message': 'No pricing rule found with that ID'}), 404

    data = request.get_json(silent=True) or {}

    if 'rateName' in data:
        rule.rate_name = data['rateName'].strip()
    if 'applicableHours' in data:
        rule.applicable_hours = data['applicableHours'].strip()
    if 'description' in data:
        rule.description = data['description'].strip()
    if 'ratePerHour' in data:
        rate, err = _parse_rate(data['ratePerHour'])
        if err:
            return jsonify({'error': 'invalid_rate', 'message': err}), 400
        rule.rate_per_hour = rate

    try:
        db.session.commit()
        return jsonify(_rule_json(rule)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.update', str(exc))
        return jsonify({'error': 'server_error',
                        'message': 'Failed to update pricing rule'}), 500


# ------------------------------------------------------------------
#  DELETE /v1/pricing/<id>
# ------------------------------------------------------------------

@pricing_bp.route('/<int:rate_id>', methods=['DELETE'])
@require_role('admin')
def delete_rule(rate_id):
    from app import db
    from models import PricingRule

    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found',
                        'message': 'No pricing rule found with that ID'}), 404

    try:
        db.session.delete(rule)
        db.session.commit()
        return '', 204
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.delete', str(exc))
        return jsonify({'error': 'server_error',
                        'message': 'Failed to delete pricing rule'}), 500


# ------------------------------------------------------------------
#  POST /v1/pricing/<id>/move  — reorder priority
# ------------------------------------------------------------------

@pricing_bp.route('/<int:rate_id>/move', methods=['POST'])
@require_role('admin')
def move_rule(rate_id):
    """Swap this rule's sort_order with its neighbour to move it up or down.

    Body: { "direction": "up" | "down" }

    "up"   = higher priority (lower sort_order index, appears first in list)
    "down" = lower priority  (higher sort_order index, appears later in list)
    """
    from app import db
    from models import PricingRule

    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found',
                        'message': 'No pricing rule found with that ID'}), 404

    data = request.get_json(silent=True) or {}
    direction = data.get('direction')
    if direction not in ('up', 'down'):
        return jsonify({'error': 'invalid_direction',
                        'message': 'direction must be "up" or "down"'}), 400

    # Fetch all rules in display order
    ordered = _ordered_rules()
    idx = next((i for i, r in enumerate(ordered) if r.rate_id == rate_id), None)
    if idx is None:
        return jsonify({'error': 'server_error'}), 500

    if direction == 'up' and idx == 0:
        return jsonify({'error': 'already_first',
                        'message': 'Rule is already at the top'}), 409
    if direction == 'down' and idx == len(ordered) - 1:
        return jsonify({'error': 'already_last',
                        'message': 'Rule is already at the bottom'}), 409

    neighbour = ordered[idx - 1] if direction == 'up' else ordered[idx + 1]

    # Swap sort_order values
    rule.sort_order, neighbour.sort_order = neighbour.sort_order, rule.sort_order

    try:
        db.session.commit()
        # Return the full updated list so the frontend can re-render in one round trip
        return jsonify([_rule_json(r) for r in _ordered_rules()]), 200
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.move', str(exc))
        return jsonify({'error': 'server_error',
                        'message': 'Failed to reorder pricing rule'}), 500
