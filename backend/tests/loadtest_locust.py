"""
loadtest_locust.py — Locust load test for GarageFlow

Simulates concurrent users hitting reservation, check-in, and payment endpoints.

Prerequisites:
  1. pip install locust
  2. Start the dev server:  cd backend && flask run
  3. Seed the database:     cd backend && python seed.py && flask seed-admin --password admin
  4. Run the load test:     locust -f backend/tests/loadtest_locust.py --host http://127.0.0.1:5000

Open http://localhost:8089 to configure users/spawn-rate and start the test.

For headless mode (CI-friendly):
  locust -f backend/tests/loadtest_locust.py --host http://127.0.0.1:5000 \
         --headless -u 50 -r 5 -t 60s --csv=loadtest_results

Endpoints tested:
  - POST /v1/tickets             (vehicle entry)
  - PUT  /v1/tickets/{id}/exit   (vehicle exit with payment)
  - POST /v1/reservations        (create reservation)
  - PUT  /v1/reservations/{id}/check (check-in)
  - POST /v1/payments/create-intent  (Stripe payment intent)
  - GET  /v1/spaces/available    (availability check)
  - GET  /health                 (baseline)
"""

import random
import string
from collections import deque
from datetime import datetime, timedelta

from locust import HttpUser, task, between, events


def _random_plate():
    """Generate a unique license plate."""
    letters = ''.join(random.choices(string.ascii_uppercase, k=3))
    digits = ''.join(random.choices(string.digits, k=4))
    return f'{letters}-{digits}'


def _future_iso(minutes=60):
    return (datetime.utcnow() + timedelta(minutes=minutes)).isoformat() + 'Z'


class GarageFlowUser(HttpUser):
    """Simulates a garage operator performing mixed operations."""
    wait_time = between(0.5, 2)

    def on_start(self):
        """Login and obtain auth token."""
        resp = self.client.post('/v1/auth/login', json={
            'username': 'admin',
            'password': 'admin',
        })
        if resp.status_code == 200:
            self.token = resp.json().get('token')
            self.headers = {'Authorization': f'Bearer {self.token}'}
        else:
            self.token = None
            self.headers = {}

        self.active_tickets = deque()
        self.active_reservations = deque()

    # ------------------------------------------------------------------
    #  Baseline / read endpoints
    # ------------------------------------------------------------------

    @task(3)
    def check_available_spaces(self):
        """GET /v1/spaces/available — lightweight read, high frequency."""
        self.client.get('/v1/spaces/available', name='/v1/spaces/available')

    @task(2)
    def health_check(self):
        """GET /health — baseline latency reference."""
        self.client.get('/health', name='/health')

    # ------------------------------------------------------------------
    #  Vehicle entry → exit workflow
    # ------------------------------------------------------------------

    @task(5)
    def vehicle_entry(self):
        """POST /v1/tickets — create a parking ticket."""
        plate = _random_plate()
        resp = self.client.post('/v1/tickets', json={
            'licensePlate': plate,
            'driverClass': random.choice(['standard', 'accessibility', 'employee']),
        }, name='/v1/tickets [POST]')

        if resp.status_code == 201:
            data = resp.json()
            self.active_tickets.append({
                'ticketId': data['ticketId'],
                'licensePlate': plate,
            })

    @task(4)
    def vehicle_exit(self):
        """PUT /v1/tickets/{id}/exit — close ticket and create payment."""
        if not self.active_tickets:
            return

        ticket = self.active_tickets.popleft()
        self.client.put(
            f'/v1/tickets/{ticket["ticketId"]}/exit',
            json={
                'licensePlate': ticket['licensePlate'],
                'paymentMethod': random.choice(['cash', 'card', 'mobile']),
            },
            name='/v1/tickets/{id}/exit',
        )

    # ------------------------------------------------------------------
    #  Reservation → check-in workflow
    # ------------------------------------------------------------------

    @task(3)
    def create_reservation(self):
        """POST /v1/reservations — book a future parking slot."""
        plate = _random_plate()
        phone = f'555-{random.randint(1000, 9999)}'
        minutes_ahead = random.randint(60, 300)

        resp = self.client.post('/v1/reservations', json={
            'phone': phone,
            'scheduledArrival': _future_iso(minutes=minutes_ahead),
            'licensePlate': plate,
            'driverClass': 'standard',
        }, name='/v1/reservations [POST]')

        if resp.status_code == 201:
            data = resp.json()
            self.active_reservations.append({
                'reservationId': data['reservationId'],
                'licensePlate': plate,
            })

    @task(2)
    def reservation_checkin(self):
        """PUT /v1/reservations/{id}/check — convert reservation to ticket."""
        if not self.active_reservations:
            return

        res = self.active_reservations.popleft()
        self.client.put(
            f'/v1/reservations/{res["reservationId"]}/check',
            json={'licensePlate': res['licensePlate']},
            name='/v1/reservations/{id}/check',
        )

    # ------------------------------------------------------------------
    #  Payment intent creation
    # ------------------------------------------------------------------

    @task(1)
    def create_payment_intent(self):
        """POST /v1/payments/create-intent — test Stripe intent creation.

        Note: This will return errors in load testing since Stripe keys
        are typically not configured. The test measures endpoint latency
        and error handling under load.
        """
        # Use a fake ticket ID — measures error-handling path throughput
        self.client.post('/v1/payments/create-intent', json={
            'ticketId': random.randint(1, 100),
        }, name='/v1/payments/create-intent')


# ======================================================================
#  Event hooks for results summary
# ======================================================================

_stats_summary = []


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """Print a summary of bottlenecks when the test ends."""
    stats = environment.runner.stats
    print('\n' + '=' * 70)
    print('  LOAD TEST RESULTS SUMMARY')
    print('=' * 70)
    print(f'  Total requests:      {stats.total.num_requests}')
    print(f'  Total failures:      {stats.total.num_failures}')
    print(f'  Avg response time:   {stats.total.avg_response_time:.0f} ms')
    print(f'  Median:              {stats.total.get_response_time_percentile(0.5):.0f} ms')
    print(f'  95th percentile:     {stats.total.get_response_time_percentile(0.95):.0f} ms')
    print(f'  99th percentile:     {stats.total.get_response_time_percentile(0.99):.0f} ms')
    print(f'  Requests/sec:        {stats.total.current_rps:.1f}')
    print(f'  Failure rate:        {stats.total.fail_ratio * 100:.1f}%')
    print()

    # Per-endpoint breakdown
    print(f'  {"Endpoint":<40} {"Avg(ms)":>8} {"P95(ms)":>8} {"Reqs":>6} {"Fail%":>6}')
    print(f'  {"-" * 40} {"-" * 8} {"-" * 8} {"-" * 6} {"-" * 6}')
    for entry in sorted(stats.entries.values(), key=lambda e: e.avg_response_time, reverse=True):
        if entry.num_requests > 0:
            fail_pct = (entry.num_failures / entry.num_requests) * 100
            print(f'  {entry.name:<40} {entry.avg_response_time:>8.0f} '
                  f'{entry.get_response_time_percentile(0.95):>8.0f} '
                  f'{entry.num_requests:>6} {fail_pct:>5.1f}%')

    print()
    print('  POTENTIAL BOTTLENECKS:')

    # Flag slow endpoints
    slow = [e for e in stats.entries.values()
            if e.avg_response_time > 500 and e.num_requests > 0]
    if slow:
        for e in slow:
            print(f'    - {e.name}: avg {e.avg_response_time:.0f}ms (>500ms threshold)')
    else:
        print('    None detected (all endpoints < 500ms avg)')

    # Flag high failure rates
    failing = [e for e in stats.entries.values()
               if e.num_requests > 0 and (e.num_failures / e.num_requests) > 0.1]
    if failing:
        print('  HIGH FAILURE RATE (>10%):')
        for e in failing:
            fail_pct = (e.num_failures / e.num_requests) * 100
            print(f'    - {e.name}: {fail_pct:.1f}% failures')

    print('=' * 70)
