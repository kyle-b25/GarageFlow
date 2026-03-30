// =============================================================
//  api.js — GarageFlow API Layer
//  All communication with the backend lives here.
// =============================================================

const BASE_URL = ''; // relative — works when served by Flask

// -------------------------------------------------------------
//  Internal helper — centralised fetch wrapper.
//  Handles JSON parsing and error codes.
// -------------------------------------------------------------
async function _request(method, path, body = null) {
  const options = {
    method,
    headers: {
      'Content-Type': 'application/json',
      // 'Authorization': `Bearer ${localStorage.getItem('token')}`,
    },
  };

  if (body) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`${BASE_URL}${path}`, options);

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const err = await response.json();
      // Surface the API's own error key if present (e.g. "garage_full")
      message = err.error || err.message || message;
    } catch (_) { /* response wasn't JSON */ }
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
//  RESERVATIONS  —  GET /v1/reservations
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
 *   status: "confirmed"|"complete"|"expired"|"cancelled"
 * }>>}
 */
export async function getReservationsByPhone(phone, includeOld = false) {
  const params = new URLSearchParams({ phone, includeOld: includeOld ? 'true' : 'false' });
  return _request('GET', `/v1/reservations?${params}`);
}

// =============================================================
//  RESERVATIONS  —  GET /v1/reservations (upcoming/confirmed)
// =============================================================

export async function getUpcomingReservations() {
  return _request('GET', '/v1/reservations');
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