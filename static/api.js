// =============================================================
//  api.js — GarageFlow API Layer
//  All communication with the backend lives here.
//  Update BASE_URL to point to your real API server.
// =============================================================

const BASE_URL = 'http://127.0.0.1:5000'; // local Flask dev server

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
 *   ticketId: string,
 *   licensePlate: string,
 *   phone?: string,
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
export async function postReservation(phone, scheduledArrival, driverClass = null) {
  const body = { phone, scheduledArrival };
  if (driverClass) body.driverClass = driverClass;
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
 * @param {boolean} includeOld  — include "complete" records (default false)
 *
 * @returns {Promise<Array<{
 *   reservationId: string,
 *   assignedFloor: number,
 *   scheduledArrival: string,
 *   status: "confirmed"|"complete"
 * }>>}
 */
export async function getReservationsByPhone(phone, includeOld = false) {
  const params = new URLSearchParams({ phone, includeOld: includeOld ? 'true' : 'false' });
  return _request('GET', `/v1/reservations?${params}`);
}


// =============================================================
//  FLOOR / AVAILABILITY  —  GET /v1/floors  (future)
// =============================================================

/**
 * GET /v1/floors
 * Fetch availability data for all floors.
 * @returns {Promise<Array<{ floor: string, total: number, zones: object }>>}
 */
export async function getAllFloors() {
  return _request('GET', '/v1/floors');
}
