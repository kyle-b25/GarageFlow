# Overview
GarageFlow is a software application designed to track and manage parking
garage occupancy while allowing customers to locate and reserve available parking spaces within
specific time and date boundaries.

The system addresses common inefficiencies in parking garage operations, where employees
monitor occupancy and drivers search for vacant spots, leading to congestion and lost revenue.
By providing real-time occupancy tracking and a reservation system, GarageFlow improves space
utilization, traffic within the garage, and enhances the customer experience.

In addition to parking operations, the system supports administrative reporting features that
provide insights into garage utilization, pricing trends, and demand patterns, enabling data-driven
decision making.

## Current Project Objectives
- Track real-time parking spot occupancy.
- Allow customers to reserve spaces within defined time windows.
- Reduce congestion my assisting with driver navigation to vacant spots.
- Improve the overall efficiency of the parking garage space.
- Generate reports to gain access to specific metrics and pricing analysis.

## Scope
This project focuses on software-based occupancy tracking and reservation management.
Sensor integration and payment processing are considered out of scope for and will be simulated.

## Backend Dev Environment Instructions for Windows
1. Create your own virtual environment. Note that the Python version is 3.14.2.
   - python -m venv venv
2. Install the libraries from requirements.txt
   - pip install -r requirements.txt
3. Create your own env file.
   - FLASK_APP=backend/app.py
   - FLASK_ENV=development
   - FLASK_DEBUG=1
   - SECRET_KEY=yoursecretkey
   - DATABASE_URL=sqlite:///database.db
   - STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key
5. Run the database setup.
   - python -c "from app import app, db; app.app_context().push(); db.create_all()"
6. Run the server.
   - flask run
7. Verify this process worked. Visit the following link in your browser.
   - http://127.0.0.1:5000/health
