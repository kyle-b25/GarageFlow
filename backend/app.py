"""
app.py — GarageFlow Main Application Entry Point

Creates the Flask app, registers all blueprints.
"""
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from datetime import timedelta
import os

load_dotenv()

app = Flask(__name__,
            template_folder='../frontend',
            static_folder='../frontend')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY') or os.urandom(32)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)

db = SQLAlchemy(app)

import models  # noqa: F401 — triggers db.create_all() for all tables

with app.app_context():
    db.create_all()


@app.route('/operator-front')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return {'status': 'ok'}


# ------------------------------------------------------------------
#  Blueprints — imported after models to avoid circular imports
# ------------------------------------------------------------------

from routes import (
    v1_bp, tickets_bp, token_auth_bp, analytics_bp,
    payments_bp, reservations_bp, spaces_bp, staff_bp, admin_bp,
)

app.register_blueprint(v1_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(token_auth_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(staff_bp)
app.register_blueprint(payments_bp)
app.register_blueprint(spaces_bp)
app.register_blueprint(reservations_bp)
app.register_blueprint(admin_bp)


# ------------------------------------------------------------------
#  CLI commands
# ------------------------------------------------------------------

@app.cli.command('seed-admin')
def seed_admin():
    """Create a default admin account (username: admin, password: admin)."""
    import bcrypt
    from models import Staff, StaffRoleEnum

    if Staff.query.filter_by(username='admin').first():
        print('Admin account already exists — skipping.')
        return

    password_hash = bcrypt.hashpw(b'admin', bcrypt.gensalt()).decode('utf-8')
    db.session.add(Staff(
        name='System Admin',
        username='admin',
        password_hash=password_hash,
        role=StaffRoleEnum.admin,
    ))
    db.session.commit()
    print('Admin account created (username: admin, password: admin).')


# ------------------------------------------------------------------
#  Background scheduler — reservation expiration
# ------------------------------------------------------------------

from apscheduler.schedulers.background import BackgroundScheduler
from tasks import expire_stale_reservations

scheduler = BackgroundScheduler()
scheduler.add_job(expire_stale_reservations, 'interval', minutes=10)
scheduler.start()


if __name__ == '__main__':
    app.run()
