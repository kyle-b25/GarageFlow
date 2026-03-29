"""
routes/ — GarageFlow API route blueprints package.

Re-exports v1_bp so the app can register it with a single import.
"""
from routes._routes import v1_bp
from routes.tickets import tickets_bp

__all__ = ['v1_bp', 'tickets_bp']
