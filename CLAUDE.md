# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment Setup

```bash
# Create and activate virtual environment
python -m venv venv
source venv/Scripts/activate  # Windows (bash)

# Install dependencies
pip install -r requirements.txt
```

## Common Commands

```bash
# Run the development server
flask run

# Seed the database (creates 1 garage → 1 floor → 5 standard spots)
python seed.py

# Create an admin staff account (interactive CLI)
flask seed-admin
```

Environment variables are loaded from `.env` via `python-dotenv`. Key vars: `SECRET_KEY`, `DATABASE_URL` (defaults to SQLite at `instance/database.db`), `STRIPE_WEBHOOK_SECRET`, `STRIPE_SECRET_KEY`.

## Architecture

**GarageFlow** is a parking garage management system. The backend is a Flask REST API backed by SQLAlchemy models. The frontend is a vanilla JS operator kiosk served at `GET /operator-front`.

### Backend

- `app.py` — Main Flask app and public API routes. Registers blueprints from `auth.py` and `staff_routes.py`. Contains `assign_spot()` / `release_spot()` helpers.
- `models.py` — All SQLAlchemy models (13 tables) and enums. Key models: `Garage`, `Floor`, `ParkingSpot`, `Vehicle`, `Ticket`, `Reservation`, `OccupancyLog`, `Staff`.
- `auth.py` — Authentication blueprint (`auth_bp`). Handles login/logout/status with Flask sessions and bcrypt. Includes IP-based rate limiting (5 failures per 60s).
- `staff_routes.py` — Staff management blueprint (`staff_bp`). Protected by `require_auth` middleware and `require_admin` decorator for RBAC.
- `seed.py` — Creates one Garage → one Floor → five ParkingSpots (all standard type).

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Health check |
| GET | `/operator-front` | — | Serve operator kiosk UI |
| POST | `/v1/tickets` | — | Vehicle entry: assign spot, create Ticket + OccupancyLog |
| GET | `/v1/tickets` | — | List tickets (filter by `?status=` or `?plate=`) |
| GET | `/v1/tickets/{id}` | — | Get single ticket |
| PUT | `/v1/tickets/{id}/exit` | — | Vehicle exit: calculate fee, release spot, create Payment |
| POST | `/v1/reservations` | — | Create reservation (advisory floor only) |
| GET | `/v1/reservations` | — | Search reservations by `?phone=` |
| POST | `/v1/auth/login` | — | Staff login (rate-limited) |
| POST | `/v1/auth/logout` | — | Staff logout |
| GET | `/v1/auth/status` | — | Check session status |
| GET | `/v1/floors` | Staff | Floor availability with zone breakdown |
| POST | `/v1/staff` | Admin | Create staff account |
| POST | `/v1/webhooks/stripe` | — | Stripe webhook (signature-verified) |

### Driver Class → Spot Type Mapping

`driverClass` values accepted by the API map to `SpotTypeEnum` as follows:
- `"standard"` → `SpotTypeEnum.standard`
- `"accessibility"` → `SpotTypeEnum.accessibility`
- `"employee"` / `"eco"` → `SpotTypeEnum.staff`

### Key Design Decisions

- **Reservations are advisory**: `POST /v1/reservations` assigns a floor number but does not lock or occupy a `ParkingSpot`. No spot record is touched.
- **OccupancyLog is append-only**: Every spot status change records a row; rows are never updated.
- **Vehicle records are reused**: `POST /v1/tickets` does `get_or_create` on `Vehicle` by license plate.
- **Floor availability counters**: `Floor.available_spots` is a denormalized counter decremented on entry and incremented on exit.
- **Spot assignment algorithm**: Floors sorted by availability ratio (available/total) descending, then first available spot of the requested type is assigned (floor-spread fairness).
- **Fee calculation**: $5.00 base + $2.00 per hour (ceiling of minutes/60). `PricingRule` table exists but is not yet wired up.
- **Auth is session-based**: Flask sessions store `operator_id`, `username`, `role`. Rate limiting is in-memory (not distributed).

### Frontend

- `templates/index.html` — Single-page kiosk UI (dark theme, orange accents). Three panels: garage entry, floor overview, reservations.
- `static/api.js` — Thin fetch wrapper exposing `postTicket`, `postReservation`, `getReservationsByPhone`, `getAllFloors`.
- `static/frontkiosk.js` — UI event handlers and DOM manipulation. Combines date + time inputs into ISO 8601 UTC before sending to the API.
