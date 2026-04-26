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
from flask import Blueprint, request, jsonify, g

from utils import log_error, login_required, require_role

payments_bp = Blueprint('payments', __name__, url_prefix='/v1/payments')

SOURCE = 'payments_module'


def _ensure_stripe_key():
    if not stripe.api_key:
        stripe.api_key = os.getenv('STRIPE_SECRET_KEY')


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
#  GET /v1/payments/config — Stripe publishable key for frontend
# ------------------------------------------------------------------

@payments_bp.route('/config', methods=['GET'])
def payment_config():
    """Return Stripe publishable key so the frontend can initialise Elements."""
    key = os.getenv('STRIPE_PUBLISHABLE_KEY', '')
    return jsonify({'publishableKey': key}), 200


# ------------------------------------------------------------------
#  POST /v1/payments/create-intent — Create Stripe PaymentIntent
# ------------------------------------------------------------------

@payments_bp.route('/create-intent', methods=['POST'])
def create_payment_intent():
    """Create a Stripe PaymentIntent for a closed ticket's fee."""
    from app import db
    from models import Ticket, Payment, TicketStatusEnum

    data = request.get_json(silent=True) or {}
    ticket_id = data.get('ticketId')

    if not ticket_id:
        return jsonify({'error': 'missing_required_field',
                        'message': 'ticketId is required'}), 400

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({'error': 'ticket_not_found',
                        'message': 'Ticket not found'}), 404
    if ticket.status != TicketStatusEnum.closed:
        return jsonify({'error': 'ticket_not_closed',
                        'message': 'Ticket must be closed before creating payment intent'}), 409
    if not ticket.total_fee or float(ticket.total_fee) <= 0:
        return jsonify({'error': 'no_fee',
                        'message': 'Ticket has no calculated fee'}), 400

    _ensure_stripe_key()
    try:
        intent = stripe.PaymentIntent.create(
            amount=int(float(ticket.total_fee) * 100),
            currency='usd',
            metadata={'ticket_id': str(ticket.ticket_id)},
        )

        # Link intent to existing payment record so webhook can find it
        payment = Payment.query.filter_by(ticket_id=ticket_id).first()
        if payment:
            payment.stripe_payment_intent_id = intent.id
            db.session.commit()

        return jsonify({
            'clientSecret': intent.client_secret,
            'paymentIntentId': intent.id,
            'amount': float(ticket.total_fee),
        }), 200
    except stripe.error.StripeError as exc:
        return jsonify({'error': 'stripe_error',
                        'message': str(exc)}), 502


# ------------------------------------------------------------------
#  POST /v1/payments — Charge a ticket
# ------------------------------------------------------------------

@payments_bp.route('', methods=['POST'])
@login_required
def create_payment():
    """Charge a closed ticket via Stripe PaymentIntent."""
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
        return jsonify({'error': 'ticket_not_found',
                        'message': 'Ticket not found'}), 404

    if ticket.status != TicketStatusEnum.closed:
        return jsonify({'error': 'ticket_not_closed',
                        'message': 'Ticket must be closed before payment'}), 409

    if ticket.payment:
        return jsonify({'error': 'duplicate_payment',
                        'message': 'Ticket already has a payment record'}), 409

    _ensure_stripe_key()
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    except stripe.error.StripeError as exc:
        return jsonify({'error': 'stripe_error',
                        'message': str(exc)}), 502

    amount = Decimal(intent.amount_received) / Decimal(100)

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
        log_error(SOURCE,f'Failed to create payment: {exc}')
        return jsonify({'error': 'server_error',
                        'message': 'Failed to save payment'}), 500

    return jsonify(_payment_json(payment)), 201


# ------------------------------------------------------------------
#  GET /v1/payments/reports — Revenue summary
# ------------------------------------------------------------------

@payments_bp.route('/reports', methods=['GET'])
@login_required
def payment_reports():
    """Revenue summary over a date range."""
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
        return jsonify({'error': 'invalid_parameter',
                        'message': 'start and end must be valid ISO 8601 datetimes'}), 400

    try:
        q = Payment.query.filter(
            Payment.payment_timestamp >= start_dt,
            Payment.payment_timestamp <= end_dt,
        )
        garage_id = request.args.get('garage_id', type=int)
        if garage_id is not None:
            from models import Ticket, ParkingSpot, Floor
            q = (q.join(Ticket, Payment.ticket_id == Ticket.ticket_id)
                   .join(ParkingSpot, Ticket.spot_id == ParkingSpot.spot_id)
                   .join(Floor, ParkingSpot.floor_id == Floor.floor_id)
                   .filter(Floor.garage_id == garage_id))
        payments = q.all()

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
        log_error(SOURCE,f'Failed to generate report: {exc}')
        return jsonify({'error': 'server_error',
                        'message': 'Failed to generate report'}), 500


# ------------------------------------------------------------------
#  GET /v1/payments/<id> — Payment details
# ------------------------------------------------------------------

@payments_bp.route('/<int:payment_id>', methods=['GET'])
@login_required
def get_payment(payment_id):
    """Return details of a single payment."""
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
@login_required
def list_payments():
    """Query payments by ticketId or license plate."""
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
        payments = Payment.query \
            .join(Ticket, Payment.ticket_id == Ticket.ticket_id) \
            .join(Vehicle, Ticket.vehicle_id == Vehicle.vehicle_id) \
            .filter(Vehicle.license_plate.ilike(plate)) \
            .order_by(Payment.payment_timestamp.desc()) \
            .all()
        return jsonify([_payment_json(p) for p in payments]), 200

    return jsonify({'error': 'missing_required_field',
                    'message': 'ticketId or plate query parameter is required'}), 400


# ------------------------------------------------------------------
#  POST /v1/payments/<id>/refund — Full or partial refund
# ------------------------------------------------------------------

@payments_bp.route('/<int:payment_id>/refund', methods=['POST'])
@login_required
def refund_payment(payment_id):
    """Process a full or partial refund via Stripe."""
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
        return jsonify({'error': 'refund_not_possible',
                        'message': 'No Stripe PaymentIntent associated with this payment'}), 400

    data = request.get_json(silent=True) or {}
    amount = data.get('amount')

    if amount is not None and Decimal(str(amount)) > payment.amount_charged:
        return jsonify({'error': 'invalid_amount',
                        'message': 'Refund amount exceeds the original charge'}), 400

    _ensure_stripe_key()
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
        log_error(SOURCE,f'Failed to update refund status: {exc}')
        return jsonify({'error': 'server_error',
                        'message': 'Refund succeeded but failed to update local record'}), 500

    log_error(SOURCE, f'Refund processed for payment {payment_id}')
    return jsonify(_payment_json(payment)), 200


# ------------------------------------------------------------------
#  POST /v1/payments/<id>/override — Admin-only manual override
# ------------------------------------------------------------------

@payments_bp.route('/<int:payment_id>/override', methods=['POST'])
@require_role('admin')
def override_payment(payment_id):
    """Admin-only manual override of payment fields."""
    from app import db
    from models import Payment, PaymentStatusEnum, PaymentMethodEnum

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
            return jsonify({'error': 'invalid_parameter',
                            'message': f'Invalid paymentStatus: {new_status}'}), 400

    if new_method:
        try:
            payment.payment_method = PaymentMethodEnum[new_method]
            changes.append(f'method={new_method}')
        except KeyError:
            return jsonify({'error': 'invalid_parameter',
                            'message': f'Invalid paymentMethod: {new_method}'}), 400

    if not changes:
        return jsonify({'error': 'invalid_parameter',
                        'message': 'No fields to override'}), 400

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_error(SOURCE,f'Override failed for payment {payment_id}: {exc}')
        return jsonify({'error': 'server_error',
                        'message': 'Failed to save override'}), 500

    log_error(SOURCE, f'Admin override on payment {payment_id} by '
              f'{g.current_user.username if g.current_user else "unknown"}: {", ".join(changes)}')
    return jsonify(_payment_json(payment)), 200
