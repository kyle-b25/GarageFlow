"""
routes/payments.py — GarageFlow Payments Blueprint

Endpoints:
  POST   /v1/payments                 — Charge a closed ticket via Stripe PaymentIntent
  GET    /v1/payments/<id>            — Payment details
  GET    /v1/payments?ticketId=       — Payment by ticket
  GET    /v1/payments?plate=          — Payment history by license plate
  POST   /v1/payments/<id>/refund     — Full or partial refund via Stripe
  GET    /v1/payments/reports         — Revenue summary over a date range
  POST   /v1/payments/<id>/override   — Admin-only manual override

Registered in app.py at url_prefix='/v1/payments'.
"""

import os
from datetime import datetime
from decimal import Decimal

import stripe
from flask import Blueprint, request, jsonify, session

payments_bp = Blueprint('payments', __name__, url_prefix='/v1/payments')

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

SOURCE = 'payments_module'


def _log_event(description):
    """Append a row to SystemEvent for audit logging."""
    try:
        from app import db
        from models import SystemEvent
        db.session.add(SystemEvent(source=SOURCE, description=description))
        db.session.commit()
    except Exception:
        pass


def _payment_json(payment):
    """Serialize a Payment row to a JSON-friendly dict."""
    return {
        'paymentId':             payment.payment_id,
        'ticketId':              payment.ticket_id,
        'amountCharged':         float(payment.amount_charged),
        'paymentMethod':         payment.payment_method.value,
        'paymentStatus':         payment.payment_status.value,
        'paymentTimestamp':      payment.payment_timestamp.isoformat() + 'Z',
        'stripePaymentIntentId': payment.stripe_payment_intent_id,
    }


# ------------------------------------------------------------------
#  POST /v1/payments — Charge a ticket
# ------------------------------------------------------------------

@payments_bp.route('', methods=['POST'])
def create_payment():
    from app import db
    from models import (
        Ticket, Payment, TicketStatusEnum,
        PaymentMethodEnum, PaymentStatusEnum,
    )

    data = request.get_json(silent=True) or {}
    ticket_id = data.get('ticketId')
    payment_intent_id = data.get('paymentIntentId')

    if not ticket_id:
        return jsonify({'error': 'missing_required_field',
                        'message': 'ticketId is required'}), 400
    if not payment_intent_id:
        return jsonify({'error': 'missing_required_field',
                        'message': 'paymentIntentId is required'}), 400

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'payment_not_found',
                        'message': 'Ticket not found'}), 404

    if ticket.status != TicketStatusEnum.closed:
        return jsonify({'error': 'ticket_not_closed',
                        'message': 'Ticket must be closed before payment'}), 409

    if ticket.payment:
        return jsonify({'error': 'ticket_not_closed',
                        'message': 'Ticket already has a payment record'}), 409

    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    except stripe.error.StripeError as exc:
        return jsonify({'error': 'stripe_error',
                        'message': str(exc)}), 502

    amount = Decimal(str(intent.amount_received / 100))

    payment = Payment(
        ticket_id=ticket.ticket_id,
        amount_charged=amount,
        payment_method=PaymentMethodEnum.card,
        payment_status=PaymentStatusEnum.paid if intent.status == 'succeeded' else PaymentStatusEnum.pending,
        stripe_payment_intent_id=payment_intent_id,
        payment_timestamp=datetime.utcnow(),
    )

    try:
        db.session.add(payment)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_event(f'Failed to create payment: {exc}')
        return jsonify({'error': 'stripe_error',
                        'message': 'Failed to save payment'}), 500

    return jsonify(_payment_json(payment)), 201


# ------------------------------------------------------------------
#  GET /v1/payments/reports — Revenue summary
# ------------------------------------------------------------------

@payments_bp.route('/reports', methods=['GET'])
def payment_reports():
    from app import db
    from models import Payment, PaymentStatusEnum

    start_str = request.args.get('start')
    end_str = request.args.get('end')

    if not start_str or not end_str:
        missing = 'start' if not start_str else 'end'
        return jsonify({'error': 'missing_required_field',
                        'message': f'{missing} query parameter is required'}), 400

    try:
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return jsonify({'error': 'missing_required_field',
                        'message': 'start and end must be valid ISO 8601 datetimes'}), 400

    try:
        payments = Payment.query.filter(
            Payment.payment_timestamp >= start_dt,
            Payment.payment_timestamp <= end_dt,
        ).all()

        total_revenue = Decimal('0.00')
        total_refunded = Decimal('0.00')
        count = 0
        paid_count = 0

        for p in payments:
            count += 1
            if p.payment_status == PaymentStatusEnum.paid:
                total_revenue += p.amount_charged
                paid_count += 1
            elif p.payment_status == PaymentStatusEnum.refunded:
                total_refunded += p.amount_charged

        return jsonify({
            'start':          start_str,
            'end':            end_str,
            'totalPayments':  count,
            'paidCount':      paid_count,
            'totalRevenue':   float(total_revenue),
            'totalRefunded':  float(total_refunded),
            'netRevenue':     float(total_revenue - total_refunded),
        }), 200
    except Exception as exc:
        db.session.rollback()
        _log_event(f'Failed to generate report: {exc}')
        return jsonify({'error': 'stripe_error',
                        'message': 'Failed to generate report'}), 500


# ------------------------------------------------------------------
#  GET /v1/payments/<id> — Payment details
# ------------------------------------------------------------------

@payments_bp.route('/<int:payment_id>', methods=['GET'])
def get_payment(payment_id):
    from models import Payment

    payment = Payment.query.get(payment_id)
    if not payment:
        return jsonify({'error': 'payment_not_found',
                        'message': 'No payment found with that ID'}), 404

    return jsonify(_payment_json(payment)), 200


# ------------------------------------------------------------------
#  GET /v1/payments?ticketId= or ?plate= — Query payments
# ------------------------------------------------------------------

@payments_bp.route('', methods=['GET'])
def list_payments():
    from models import Payment, Ticket, Vehicle

    ticket_id = request.args.get('ticketId')
    plate = request.args.get('plate')

    if ticket_id:
        payment = Payment.query.filter_by(ticket_id=ticket_id).first()
        if not payment:
            return jsonify({'error': 'payment_not_found',
                            'message': 'No payment found for that ticket'}), 404
        return jsonify(_payment_json(payment)), 200

    if plate:
        vehicle = Vehicle.query.filter(Vehicle.license_plate.ilike(plate)).first()
        if not vehicle:
            return jsonify([]), 200

        ticket_ids = [t.ticket_id for t in
                      Ticket.query.filter_by(vehicle_id=vehicle.vehicle_id).all()]
        if not ticket_ids:
            return jsonify([]), 200

        payments = Payment.query.filter(Payment.ticket_id.in_(ticket_ids)) \
            .order_by(Payment.payment_timestamp.desc()).all()
        return jsonify([_payment_json(p) for p in payments]), 200

    return jsonify({'error': 'missing_required_field',
                    'message': 'ticketId or plate query parameter is required'}), 400


# ------------------------------------------------------------------
#  POST /v1/payments/<id>/refund — Full or partial refund
# ------------------------------------------------------------------

@payments_bp.route('/<int:payment_id>/refund', methods=['POST'])
def refund_payment(payment_id):
    from app import db
    from models import Payment, PaymentStatusEnum

    payment = Payment.query.get(payment_id)
    if not payment:
        return jsonify({'error': 'payment_not_found',
                        'message': 'No payment found with that ID'}), 404

    if payment.payment_status == PaymentStatusEnum.refunded:
        return jsonify({'error': 'already_refunded',
                        'message': 'Payment has already been refunded'}), 409

    if not payment.stripe_payment_intent_id:
        return jsonify({'error': 'stripe_error',
                        'message': 'No Stripe PaymentIntent associated with this payment'}), 400

    data = request.get_json(silent=True) or {}
    amount = data.get('amount')

    try:
        refund_kwargs = {'payment_intent': payment.stripe_payment_intent_id}
        if amount is not None:
            refund_kwargs['amount'] = int(Decimal(str(amount)) * 100)

        stripe.Refund.create(**refund_kwargs)
    except stripe.error.StripeError as exc:
        return jsonify({'error': 'stripe_error',
                        'message': str(exc)}), 502

    payment.payment_status = PaymentStatusEnum.refunded

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_event(f'Failed to update refund status: {exc}')
        return jsonify({'error': 'stripe_error',
                        'message': 'Refund succeeded but failed to update local record'}), 500

    _log_event(f'Refund processed for payment {payment_id}')
    return jsonify(_payment_json(payment)), 200


# ------------------------------------------------------------------
#  POST /v1/payments/<id>/override — Admin-only manual override
# ------------------------------------------------------------------

@payments_bp.route('/<int:payment_id>/override', methods=['POST'])
def override_payment(payment_id):
    from app import db
    from models import Payment, PaymentStatusEnum, PaymentMethodEnum

    if session.get('role') != 'admin':
        return jsonify({'error': 'insufficient_permissions',
                        'message': 'Admin access required'}), 403

    payment = Payment.query.get(payment_id)
    if not payment:
        return jsonify({'error': 'payment_not_found',
                        'message': 'No payment found with that ID'}), 404

    data = request.get_json(silent=True) or {}
    new_amount = data.get('amountCharged')
    new_status = data.get('paymentStatus')
    new_method = data.get('paymentMethod')

    changes = []

    if new_amount is not None:
        payment.amount_charged = Decimal(str(new_amount))
        changes.append(f'amount={new_amount}')

    if new_status:
        try:
            payment.payment_status = PaymentStatusEnum[new_status]
            changes.append(f'status={new_status}')
        except KeyError:
            return jsonify({'error': 'missing_required_field',
                            'message': f'Invalid paymentStatus: {new_status}'}), 400

    if new_method:
        try:
            payment.payment_method = PaymentMethodEnum[new_method]
            changes.append(f'method={new_method}')
        except KeyError:
            return jsonify({'error': 'missing_required_field',
                            'message': f'Invalid paymentMethod: {new_method}'}), 400

    if not changes:
        return jsonify({'error': 'missing_required_field',
                        'message': 'No fields to override'}), 400

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _log_event(f'Override failed for payment {payment_id}: {exc}')
        return jsonify({'error': 'stripe_error',
                        'message': 'Failed to save override'}), 500

    _log_event(
        f'Admin override on payment {payment_id} by '
        f'{session.get("username", "unknown")}: {", ".join(changes)}'
    )
    return jsonify(_payment_json(payment)), 200
