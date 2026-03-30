"""
routes/ — GarageFlow API route blueprints package.

Re-exports all blueprints so app.py can register them with a single import.
"""
from routes._routes import v1_bp
from routes.tickets import tickets_bp
from routes.auth import token_auth_bp
from routes.analytics import analytics_bp
from routes.payments import payments_bp
from routes.reservations import reservations_bp
from routes.spaces import spaces_bp
from routes.staff import staff_bp
from routes.admin import admin_bp

__all__ = [
    'v1_bp', 'tickets_bp', 'token_auth_bp', 'analytics_bp',
    'payments_bp', 'reservations_bp', 'spaces_bp', 'staff_bp', 'admin_bp',
]
