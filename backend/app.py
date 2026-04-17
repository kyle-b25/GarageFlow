"""
app.py — GarageFlow Main Application Entry Point

Creates the Flask app, registers all blueprints.
"""
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv
from datetime import timedelta
import click
import os

load_dotenv()

app = Flask(__name__,
            template_folder='../frontend',
            static_folder='../frontend')
_secret = os.getenv('SECRET_KEY')
if not _secret and os.getenv('FLASK_ENV') not in ('development', 'testing', None):
    raise RuntimeError(
        'SECRET_KEY environment variable is required in non-development environments. '
        'Set it in your .env file or environment.'
    )
app.config['SECRET_KEY'] = _secret or os.urandom(32)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

import models  # noqa: F401 — registers all models with SQLAlchemy metadata


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

@app.cli.command('init-db')
def init_db():
    """Create tables via db.create_all() (legacy fallback).

    Prefer `flask db upgrade` for managed migrations. This command
    still works for bootstrapping a fresh SQLite database without
    running through the full Alembic history.
    """
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    db.create_all()

    with db.engine.connect() as conn:
        table_names = sorted(db.inspect(db.engine).get_table_names())
    print(f"Database: {db_url}")
    print(f"Tables ({len(table_names)}):")
    for name in table_names:
        print(f"  {name}")
    print("init-db complete.")


@app.cli.command('seed-admin')
@click.option('--password', prompt='Admin password', hide_input=True,
              confirmation_prompt=True, help='Password for the admin account.')
def seed_admin(password):
    """Create the super-admin account. Requires a password argument.

    In development, you can pass --password on the command line.
    Refuses to run with a trivial password outside FLASK_ENV=development.
    """
    import bcrypt
    from models import Staff, StaffRoleEnum

    env = os.getenv('FLASK_ENV', 'development')

    # Block weak passwords in non-development environments
    if env != 'development' and len(password) < 12:
        print('ERROR: Password must be at least 12 characters in non-development environments.')
        return

    if Staff.query.filter_by(username='admin').first():
        print('Admin account already exists — skipping.')
        return

    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    db.session.add(Staff(
        name='System Admin',
        username='admin',
        password_hash=password_hash,
        role=StaffRoleEnum.admin,
        is_super_admin=True,
    ))
    db.session.commit()
    print('Super-admin account created (username: admin).')


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
