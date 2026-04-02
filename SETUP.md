# GarageFlow — Setup & Run

## Prerequisites

- Python 3.10+ (3.14 works but any 3.10+ is fine)
- Git

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd my-api-project

# 2. Create and activate a virtual environment
python -m venv venv
source venv/Scripts/activate   # Windows (Git Bash / MSYS2)
# source venv/bin/activate     # macOS / Linux

# 3. Install dependencies
pip install -r backend/requirements.txt

# 4. Create the .env file in backend/
cat > backend/.env << 'EOF'
FLASK_APP=app.py
FLASK_ENV=development
FLASK_DEBUG=1
SECRET_KEY=yoursecretkey
DATABASE_URL=sqlite:///database.db
EOF

# 5. Initialize the database
cd backend
flask init-db

# 6. Seed the database (1 garage, 1 floor, 5 parking spots)
python seed.py

# 7. (Optional) Create an admin staff account
flask seed-admin

# 8. Start the server
flask run
```

## Verify

```bash
curl http://127.0.0.1:5000/health
# => {"status": "ok"}
```

## Access the Kiosk UI

Open http://127.0.0.1:5000/operator-front in a browser.

## Run Tests

```bash
cd backend
pytest
```
