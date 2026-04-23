"""
notifications.py — GarageFlow Notification Service

Sends email (SendGrid) and SMS (Twilio) alerts for:
  - Reservation confirmation
  - Payment receipt
  - System alerts (capacity, gate faults)

All methods degrade gracefully when credentials are missing.
"""

import os
import sys


# ─── Configuration ───────────────────────────────────────────────

SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
NOTIFICATION_FROM_EMAIL = os.getenv('NOTIFICATION_FROM_EMAIL', 'noreply@garageflow.example.com')

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER')


def _sendgrid_available():
    return bool(SENDGRID_API_KEY)


def _twilio_available():
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER)


# ─── Low-level senders ──────────────────────────────────────────

def send_email(to_email, subject, body_text, body_html=None):
    """Send an email via SendGrid. Returns True on success, False on failure/unavailable."""
    if not _sendgrid_available():
        print('[NOTIFY] SendGrid not configured — skipping email', file=sys.stderr)
        return False

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Content

        message = Mail(
            from_email=NOTIFICATION_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
        )
        message.add_content(Content('text/plain', body_text))
        if body_html:
            message.add_content(Content('text/html', body_html))

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return 200 <= response.status_code < 300
    except Exception as exc:
        print(f'[NOTIFY] SendGrid error: {exc}', file=sys.stderr)
        return False


def send_sms(to_number, message_body):
    """Send an SMS via Twilio. Returns True on success, False on failure/unavailable."""
    if not _twilio_available():
        print('[NOTIFY] Twilio not configured — skipping SMS', file=sys.stderr)
        return False

    try:
        from twilio.rest import Client

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=message_body,
            from_=TWILIO_FROM_NUMBER,
            to=to_number,
        )
        return True
    except Exception as exc:
        print(f'[NOTIFY] Twilio error: {exc}', file=sys.stderr)
        return False


# ─── High-level notification methods ────────────────────────────

def notify_reservation_confirmed(reservation, customer=None):
    """Send reservation confirmation via email and/or SMS."""
    res_id = f'R-{reservation.reservation_id:04d}'
    arrival = reservation.start_datetime.strftime('%B %d, %Y at %I:%M %p')
    floor = reservation.floor_number
    fee = f'${float(reservation.quoted_fee):.2f}' if reservation.quoted_fee else 'TBD'

    subject = f'GarageFlow — Reservation {res_id} Confirmed'
    body = (
        f'Your reservation {res_id} is confirmed.\n\n'
        f'Arrival: {arrival}\n'
        f'Floor: {floor}\n'
        f'Quoted Fee: {fee}\n\n'
        f'Please arrive on time. Your reservation will expire if you do not check in.'
    )

    if customer and customer.email and not customer.email.endswith('@placeholder.local'):
        send_email(customer.email, subject, body)

    phone = reservation.phone
    if phone:
        send_sms(phone, f'GarageFlow: Reservation {res_id} confirmed for {arrival}. Floor {floor}. Fee: {fee}.')


def notify_payment_receipt(ticket, payment):
    """Send payment receipt via email and/or SMS."""
    amount = f'${float(payment.amount_charged):.2f}'
    method = payment.payment_method.value
    ticket_id = ticket.ticket_id

    subject = f'GarageFlow — Payment Receipt (Ticket #{ticket_id})'
    body = (
        f'Payment received for Ticket #{ticket_id}.\n\n'
        f'Amount: {amount}\n'
        f'Method: {method}\n'
        f'Duration: {ticket.duration} minutes\n\n'
        f'Thank you for parking with GarageFlow.'
    )

    # Attempt to find customer email via vehicle -> customer relationship
    vehicle = ticket.vehicle
    if vehicle and vehicle.customer and vehicle.customer.email:
        if not vehicle.customer.email.endswith('@placeholder.local'):
            send_email(vehicle.customer.email, subject, body)

    if ticket.phone:
        send_sms(ticket.phone, f'GarageFlow: Payment of {amount} received for Ticket #{ticket_id}. Method: {method}.')


def notify_system_alert(subject, message, admin_emails=None):
    """Send a system alert to specified admin emails.

    Args:
        subject: Alert subject line.
        message: Alert body text.
        admin_emails: List of email addresses. If None, logs to stderr only.
    """
    print(f'[ALERT] {subject}: {message}', file=sys.stderr)

    if not admin_emails:
        return

    for email in admin_emails:
        send_email(email, f'GarageFlow Alert — {subject}', message)
