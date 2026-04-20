"""
routes/pricing.py — Pricing Rule CRUD (admin-only)

Endpoints:
  GET    /v1/pricing          — List all pricing rules
  POST   /v1/pricing          — Create pricing rule
  GET    /v1/pricing/<id>     — Get single rule
  PUT    /v1/pricing/<id>     — Update rule
  DELETE /v1/pricing/<id>     — Delete rule
"""

from flask import Blueprint, request, jsonify

from utils import require_role, log_error, PRICING_PROGRAMS

pricing_bp = Blueprint('pricing', __name__, url_prefix='/v1/pricing')


def _rule_json(rule):
    return {
        'rateId': rule.rate_id,
        'rateName': rule.rate_name,
        'applicableHours': rule.applicable_hours,
        'pricingModel': rule.pricing_model.value,
        'description': rule.description,
        'program': rule.program,
    }


@pricing_bp.route('', methods=['GET'])
def list_rules():
    from models import PricingRule
    try:
        rules = PricingRule.query.all()
        return jsonify([_rule_json(r) for r in rules]), 200
    except Exception as exc:
        log_error('pricing.list', str(exc))
        return jsonify({'error': 'server_error'}), 500


@pricing_bp.route('', methods=['POST'])
@require_role('admin')
def create_rule():
    from app import db
    from models import PricingRule, PricingModelEnum

    data = request.get_json(silent=True) or {}
    required = ('rateName', 'applicableHours', 'pricingModel', 'description', 'program')
    for field in required:
        if not data.get(field):
            return jsonify({'error': 'missing_required_field', 'message': f'{field} is required'}), 400

    model_val = data['pricingModel']
    valid_models = {m.value for m in PricingModelEnum}
    if model_val not in valid_models:
        return jsonify({'error': 'invalid_pricing_model',
                        'message': f'pricingModel must be one of: {", ".join(sorted(valid_models))}'}), 400

    program = data['program']
    if program not in PRICING_PROGRAMS:
        return jsonify({'error': 'invalid_program',
                        'message': f'program must be one of: {", ".join(sorted(PRICING_PROGRAMS))}'}), 400

    try:
        rule = PricingRule(
            rate_name=data['rateName'],
            applicable_hours=data['applicableHours'],
            pricing_model=PricingModelEnum(model_val),
            description=data['description'],
            program=program,
        )
        db.session.add(rule)
        db.session.commit()
        return jsonify(_rule_json(rule)), 201
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.create', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to create pricing rule'}), 500


@pricing_bp.route('/<int:rate_id>', methods=['GET'])
def get_rule(rate_id):
    from models import PricingRule

    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found', 'message': 'No pricing rule found with that ID'}), 404
    return jsonify(_rule_json(rule)), 200


@pricing_bp.route('/<int:rate_id>', methods=['PUT'])
@require_role('admin')
def update_rule(rate_id):
    from app import db
    from models import PricingRule, PricingModelEnum

    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found', 'message': 'No pricing rule found with that ID'}), 404

    data = request.get_json(silent=True) or {}

    if 'rateName' in data:
        rule.rate_name = data['rateName']
    if 'applicableHours' in data:
        rule.applicable_hours = data['applicableHours']
    if 'pricingModel' in data:
        valid_models = {m.value for m in PricingModelEnum}
        if data['pricingModel'] not in valid_models:
            return jsonify({'error': 'invalid_pricing_model'}), 400
        rule.pricing_model = PricingModelEnum(data['pricingModel'])
    if 'description' in data:
        rule.description = data['description']
    if 'program' in data:
        if data['program'] not in PRICING_PROGRAMS:
            return jsonify({'error': 'invalid_program',
                            'message': f'program must be one of: {", ".join(sorted(PRICING_PROGRAMS))}'}), 400
        rule.program = data['program']

    try:
        db.session.commit()
        return jsonify(_rule_json(rule)), 200
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.update', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to update pricing rule'}), 500


@pricing_bp.route('/<int:rate_id>', methods=['DELETE'])
@require_role('admin')
def delete_rule(rate_id):
    from app import db
    from models import PricingRule

    rule = PricingRule.query.get(rate_id)
    if not rule:
        return jsonify({'error': 'rule_not_found', 'message': 'No pricing rule found with that ID'}), 404

    try:
        db.session.delete(rule)
        db.session.commit()
        return '', 204
    except Exception as exc:
        db.session.rollback()
        log_error('pricing.delete', str(exc))
        return jsonify({'error': 'server_error', 'message': 'Failed to delete pricing rule'}), 500
