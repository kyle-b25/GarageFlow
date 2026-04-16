"""
tests/test_frontend.py — Frontend integration tests (Task 38)

Verifies that the HTML served by Flask contains the expected elements,
forms, and structure. Tests use the Flask test client (no browser needed).

Run: pytest tests/test_frontend.py -v
"""

import pytest
from app import app, db


@pytest.fixture()
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    app.config['SECRET_KEY'] = 'test-secret'
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


class TestFrontendPageLoad:
    """Verify the kiosk page loads and contains expected elements."""

    def test_operator_front_returns_200(self, client):
        resp = client.get('/operator-front')
        assert resp.status_code == 200

    def test_page_contains_entry_form(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'id="entry-plate"' in html
        assert 'id="vehicle-type"' in html
        assert 'id="btn-entry-submit"' in html

    def test_page_contains_reservation_form(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'id="res-phone"' in html
        assert 'id="res-arrival-date"' in html
        assert 'id="res-plate"' in html
        assert 'id="btn-res-submit"' in html

    def test_page_contains_floor_overview(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'id="panel-floor"' in html
        assert 'id="btn-floor-show"' in html
        assert 'id="floor-cards"' in html

    def test_page_contains_confirmation_panel(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'id="entry-confirmation"' in html

    def test_page_contains_dashboard(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'id="panel-dashboard"' in html
        assert 'id="dash-user"' in html

    def test_page_contains_driver_class_options(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        for option in ['standard', 'accessibility', 'employee', 'eco']:
            assert f'value="{option}"' in html

    def test_page_loads_app_js(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'app.js' in html

    def test_page_has_garage_name_element(self, client):
        resp = client.get('/operator-front')
        html = resp.data.decode()
        assert 'id="garage-name"' in html


class TestFrontendAPIIntegration:
    """Verify frontend-facing API endpoints return expected shapes."""

    def test_capacity_endpoint_shape(self, client):
        resp = client.get('/v1/capacity')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'total' in data
        assert 'occupied' in data
        assert 'available' in data
        assert 'byType' in data

    def test_health_endpoint(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'ok'

    def test_floors_endpoint_empty_db(self, client):
        resp = client.get('/v1/floors')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_garage_endpoint_empty_db(self, client):
        resp = client.get('/v1/garage')
        # May return 404 or 200 with null depending on implementation
        assert resp.status_code in (200, 404)
