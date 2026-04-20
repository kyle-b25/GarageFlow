// =============================================================
//  api.js — GarageFlow API Layer
//  All communication with the backend lives here.
// =============================================================

const BASE_URL = ''; // relative — works when served by Flask


// -------------------------------------------------------------
//  Token state — managed by app.js via setters/getters
// -------------------------------------------------------------
let _token = null;
let _tokenExpiresAt = null;
let _refreshTimer = null;
let _onTokenClear = null;   // callback set by app.js

export function setAuthState(token, expiresAt, onClear) {
  _token = token;
  _tokenExpiresAt = expiresAt ? new Date(expiresAt) : null;
  if (onClear) _onTokenClear = onClear;
  _scheduleRefresh();
}

export function getStoredToken() { return _token; }

export function clearAuthState() {
  _token = null;
  _tokenExpiresAt = null;
  if (_refreshTimer) { clearTimeout(_refreshTimer); _refreshTimer = null; }
  if (_onTokenClear) _onTokenClear();
}

function _scheduleRefresh() {
  if (_refreshTimer) clearTimeout(_refreshTimer);
  if (!_tokenExpiresAt || !_token) return;
  const msUntilExpiry = _tokenExpiresAt.getTime() - Date.now();
  const refreshAt = Math.max(msUntilExpiry * 0.75, 5000);
  _refreshTimer = setTimeout(async () => {
    try {
      const res = await _authRequest('POST', '/v1/auth/refresh', null, _token);
      _token = res.token;
      _tokenExpiresAt = new Date(res.expiresAt);
      _scheduleRefresh();
    } catch (_e) {
      clearAuthState();
    }
  }, refreshAt);
}


// -------------------------------------------------------------
//  Internal helpers — centralised fetch wrappers.
//  Handles JSON parsing and error codes.
// -------------------------------------------------------------
async function _authRequest(method, path, body = null, token = null) {
  const t = token || _token;
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (t) options.headers['Authorization'] = `Bearer ${t}`;
  if (body) options.body = JSON.stringify(body);
  const response = await fetch(`${BASE_URL}${path}`, options);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { const err = await response.json(); message = err.error || err.message || message; } catch (_e) {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

/** Authenticated request with auto-retry on 401 via token refresh. */
async function _authRetry(method, path, body = null) {
  try {
    return await _authRequest(method, path, body);
  } catch (err) {
    if (err.status === 401 && _token) {
      try {
        const res = await _authRequest('POST', '/v1/auth/refresh', null, _token);
        _token = res.token;
        _tokenExpiresAt = new Date(res.expiresAt);
        _scheduleRefresh();
        return await _authRequest(method, path, body);
      } catch (_e) {
        clearAuthState();
        throw new Error('Session expired. Please log in again.');
      }
    }
    throw err;
  }
}

async function _request(method, path, body = null) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };

  if (body) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`${BASE_URL}${path}`, options);

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const err = await response.json();
      message = err.error || err.message || message;
    } catch (_e) { /* response wasn't JSON */ }
    throw new Error(message);
  }

  if (response.status === 204) return null;
  return response.json();
}


// =============================================================
//  GARAGE ENTRY  —  POST /v1/tickets
// =============================================================

/**
 * POST /v1/tickets
 * Record a vehicle entering the garage.
 *
 * @param {string} licensePlate  — required, e.g. "ABC123"
 * @param {string} driverClass   — required: "standard"|"accessibility"|"employee"|"eco"
 * @param {string} [phone]       — optional, walk-up customers may omit
 *
 * @returns {Promise<{
 *   ticketId: number,
 *   licensePlate: string,
 *   assignedFloor: number,
 *   entryTime: string,   // ISO 8601
 *   status: "active"
 * }>}
 *
 * Error keys: missing_required_field | garage_full | duplicate_plate | server_error
 */
export async function postTicket(licensePlate, driverClass, phone = null) {
  const body = { licensePlate, driverClass };
  if (phone) body.phone = phone;
  return _request('POST', '/v1/tickets', body);
}


// =============================================================
//  TICKETS  —  GET + EXIT
// =============================================================

/** GET /v1/tickets?plate=X — look up active tickets by plate. */
export async function getTicketsByPlate(plate) {
  const params = new URLSearchParams({ plate, status: 'active' });
  return _request('GET', `/v1/tickets?${params}`);
}

/**
 * PUT /v1/tickets/{id}/exit — process vehicle exit.
 * @returns {{ ticketId, licensePlate, exitTime, duration, totalFee, paymentStatus, status }}
 */
export async function exitTicket(ticketId, licensePlate, paymentMethod) {
  return _request('PUT', `/v1/tickets/${ticketId}/exit`, { licensePlate, paymentMethod });
}


// =============================================================
//  RESERVATIONS  —  POST /v1/reservations
// =============================================================

/**
 * POST /v1/reservations
 * Create a new parking reservation.
 *
 * @param {string} phone             — required, for SMS and lookup
 * @param {string} scheduledArrival  — required, ISO 8601 e.g. "2026-12-03T10:00:00Z"
 * @param {string} [driverClass]     — optional: "standard"|"accessibility"|"employee"|"eco"
 *
 * @returns {Promise<{
 *   reservationId: string,   // e.g. "R-0067"
 *   assignedFloor: number,   // -1 = basement floor 1
 *   scheduledArrival: string,
 *   status: "confirmed"
 * }>}
 *
 * Error keys: missing_required_field | invalid_scheduled_arrival | garage_full | server_error
 */
export async function postReservation(phone, scheduledArrival, driverClass = null, licensePlate = null) {
  const body = { phone, scheduledArrival };
  if (driverClass) body.driverClass = driverClass;
  if (licensePlate) body.licensePlate = licensePlate;
  return _request('POST', '/v1/reservations', body);
}


// =============================================================
//  RESERVATIONS  —  GET / CHECK-IN / CANCEL
// =============================================================

/**
 * GET /v1/reservations?phone=XXX&includeOld=true
 * Search reservations by phone number.
 *
 * @param {string}  phone       — phone number to look up
 * @param {boolean} includeOld  — include fulfilled, expired, and cancelled records (default false)
 *
 * @returns {Promise<Array<{
 *   reservationId: string,
 *   assignedFloor: number,
 *   scheduledArrival: string,
 *   status: "confirmed"|"fulfilled"|"expired"|"cancelled"
 * }>>}
 */
export async function getReservationsByPhone(phone, includeOld = false) {
  const params = new URLSearchParams({ phone, includeOld: includeOld ? 'true' : 'false' });
  return _request('GET', `/v1/reservations?${params}`);
}

export async function getUpcomingReservations() {
  return _request('GET', '/v1/reservations');
}

/** PUT /v1/reservations/{id}/check — check in a confirmed reservation. */
export async function checkInReservation(reservationId, licensePlate) {
  return _request('PUT', `/v1/reservations/${reservationId}/check`, { licensePlate });
}

/** DELETE /v1/reservations/{id} — cancel with identity verification. */
export async function cancelReservation(reservationId, licensePlate, phone) {
  return _request('DELETE', `/v1/reservations/${reservationId}`, { licensePlate, phone });
}


// =============================================================
//  FLOOR / AVAILABILITY  —  GET /v1/floors
// =============================================================

/**
 * GET /v1/floors
 * Fetch availability data for all floors.
 * @returns {Promise<Array<{ floor: string, available: number, zones: object }>>}
 */
export async function getAllFloors() {
  return _request('GET', '/v1/floors');
}

// =============================================================
//  GARAGE INFO  —  GET /v1/garage
// =============================================================

/**
 * GET /v1/garage
 * Fetch top-level garage metadata (name, capacity, hours, etc.)
 * @returns {Promise<{
 *   garageId: number,
 *   name: string,
 *   totalCapacity: number,
 *   numberOfFloors: number,
 *   operatingHours: string,
 *   frontDeskPhone: string
 * }>}
 */
export async function getGarage() {
  return _request('GET', '/v1/garage');
}


// =============================================================
//  AUTH  —  Login / Refresh / Logout
// =============================================================

export async function loginStaff(username, password) {
  return _request('POST', '/v1/auth/login', { username, password });
}

/** POST /v1/auth/refresh — exchange current token for a new one. */
export async function refreshToken() {
  return _authRequest('POST', '/v1/auth/refresh');
}

/** POST /v1/auth/logout — revoke current token. */
export async function logoutStaff() {
  return _authRequest('POST', '/v1/auth/logout');
}


// =============================================================
//  ANALYTICS  —  Dashboard endpoints (auth required)
// =============================================================

export async function getOccupancy(token) {
  return _authRequest('GET', '/v1/analytics/occupancy', null, token);
}

export async function getRevenue(token, from, to) {
  const params = new URLSearchParams({ from, to });
  return _authRequest('GET', `/v1/analytics/revenue?${params}`, null, token);
}

export async function getUtilization(token, from, to) {
  const params = new URLSearchParams({ from, to });
  return _authRequest('GET', `/v1/analytics/utilization?${params}`, null, token);
}

export async function getPeakHours(token, from, to) {
  const params = new URLSearchParams({ from, to });
  return _authRequest('GET', `/v1/analytics/peak-hours?${params}`, null, token);
}

export async function getCapacityAlert() {
  return _request('GET', '/v1/capacity/alert');
}


// =============================================================
//  PAYMENTS  —  Stripe integration
// =============================================================

/** GET /v1/payments/config — Stripe publishable key. */
export async function getPaymentConfig() {
  return _request('GET', '/v1/payments/config');
}

/** POST /v1/payments/create-intent — create PaymentIntent for a ticket. */
export async function createPaymentIntent(ticketId) {
  return _request('POST', '/v1/payments/create-intent', { ticketId });
}


// =============================================================
//  ADMIN  —  Staff CRUD, Audit, Garage Config (auth required)
// =============================================================

/** GET /v1/users — list all staff accounts. */
export async function getUsers() {
  return _authRetry('GET', '/v1/users');
}

/** POST /v1/auth/register — create a new staff account (admin). */
export async function registerStaff(data) {
  return _authRetry('POST', '/v1/auth/register', data);
}

/** PUT /v1/users/{id}/role — change a staff member's role. */
export async function changeUserRole(userId, role) {
  return _authRetry('PUT', `/v1/users/${userId}/role`, { role });
}

/** PUT /v1/users/{id}/status — activate or deactivate a staff account. */
export async function changeUserStatus(userId, status) {
  return _authRetry('PUT', `/v1/users/${userId}/status`, { status });
}

/** GET /v1/admin/history — query audit log. */
export async function getAuditHistory(params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) { if (v) qs.set(k, v); }
  return _authRetry('GET', `/v1/admin/history?${qs}`);
}

/** POST /v1/garage — create a new garage (admin). */
export async function createGarageAPI(data) {
  return _authRetry('POST', '/v1/garage', data);
}
