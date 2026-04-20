# GarageFlow — Setup & Launch Guide

## Prerequisites

- **Python 3.10+** (3.14 works but any 3.10+ is fine)
- **Git**
- **Stripe test keys** (optional — needed only for payment processing)

---

## Quick Start (Command Prompt / Terminal)

Copy and paste each block below into your terminal.

### 1. Clone the repo

```bash
git clone https://github.com/kyle-b25/GarageFlow.git
cd GarageFlow
```

### 2. Create and activate a virtual environment

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate
```

**Windows (Git Bash / PowerShell):**
```bash
python -m venv venv
source venv/Scripts/activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r backend/requirements.txt
```

### 4. Create the environment file

Create a file at `backend/.env` with the following contents:

**Windows (Command Prompt):**
```cmd
(
echo FLASK_APP=app.py
echo FLASK_ENV=development
echo FLASK_DEBUG=1
echo SECRET_KEY=dev-secret-change-in-production
echo DATABASE_URL=sqlite:///database.db
echo STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key
echo STRIPE_PUBLISHABLE_KEY=pk_test_your_stripe_publishable_key
echo CORS_ORIGINS=
) > backend\.env
```

**macOS / Linux / Git Bash:**
```bash
cat > backend/.env << 'EOF'
FLASK_APP=app.py
FLASK_ENV=development
FLASK_DEBUG=1
SECRET_KEY=dev-secret-change-in-production
DATABASE_URL=sqlite:///database.db
STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key
STRIPE_PUBLISHABLE_KEY=pk_test_your_stripe_publishable_key
CORS_ORIGINS=
EOF
```

> Replace the `sk_test_...` and `pk_test_...` values with your actual Stripe test keys from https://dashboard.stripe.com/test/apikeys. If you don't have Stripe keys, leave the placeholder values — the app will run fine, but payment processing will fail.

> `CORS_ORIGINS` left blank allows all origins (fine for local dev). In production, set it to a comma-separated list of allowed origins (e.g. `https://yourdomain.com`).

### 5. Initialize and seed the database

```bash
cd backend
flask init-db
python seed.py
```

This creates a SQLite database with 1 garage, 1 floor, and 5 parking spots.

### 6. Create an admin account

```bash
flask seed-admin --password admin
```

This creates a super-admin account with username `admin` and password `admin`. In development you can use any password.

### 7. Launch the server

```bash
flask run
```

The server starts at **http://127.0.0.1:5000**.

---

## Verify It Works

Open a new terminal (keep the server running) and run:

```bash
curl http://127.0.0.1:5000/health
```

Expected output:
```json
{"status": "ok"}
```

---

## Access the Operator Kiosk UI

Open in your browser:

```
http://127.0.0.1:5000/operator-front
```

The kiosk has panels for:
- **Garage Entry** — register a vehicle entering the garage
- **Vehicle Exit** — process exit with fee calculation and Stripe payment
- **Floor Overview** — live availability by floor (auto-refreshes)
- **Reservations** — create, check-in, and cancel reservations
- **Admin Dashboard** — login with admin credentials to access analytics, staff management, audit log, and garage configuration

---

## Run Tests

```bash
cd backend
pytest
```

Run a specific test file:
```bash
pytest tests/test_all.py -v
```

Run a specific test class or method:
```bash
pytest tests/test_all.py::TestTicketCreation -v
pytest tests/test_workflows.py::TestVehicleEntry::test_entry_standard_driver -v
```

---

## Database Migrations

Schema changes are managed with Flask-Migrate (Alembic). Run from `backend/`:

```bash
# Generate a migration after changing models.py
flask db migrate -m "description of change"

# Apply pending migrations
flask db upgrade

# Downgrade one revision
flask db downgrade
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `FLASK_APP` | Yes | — | Must be `app.py` |
| `FLASK_ENV` | No | `development` | `development` or `production` |
| `FLASK_DEBUG` | No | `0` | `1` enables debug mode with auto-reload |
| `SECRET_KEY` | Yes* | random | Session signing key. *Required in production (min 12 chars) |
| `DATABASE_URL` | No | `sqlite:///database.db` | SQLAlchemy connection string |
| `STRIPE_SECRET_KEY` | No | — | Stripe secret key for payment processing |
| `STRIPE_PUBLISHABLE_KEY` | No | — | Stripe publishable key (sent to frontend) |
| `CORS_ORIGINS` | No | `*` (all) | Comma-separated allowed origins |
