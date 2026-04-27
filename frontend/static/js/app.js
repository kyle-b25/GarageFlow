// =============================================================
//  app.js — GarageFlow UI Logic
//  Handles DOM interactions and calls api.js for data.
//  No fetch() calls here — all API calls go through api.js.
// =============================================================

import {
  postTicket,
  postReservation,
  getReservationsByPhone,
  getAllFloors,
  getUpcomingReservations,
  getGarage,
  loginStaff,
  logoutStaff,
  getOccupancy,
  getRevenue,
  getUtilization,
  getPeakHours,
  getCapacityAlert,
  getTicketsByPlate,
  getClosedTickets,
  overrideTicket,
  exitTicket,
  getPaymentConfig,
  createPaymentIntent,
  checkInReservation,
  cancelReservation,
  getUsers,
  registerStaff,
  changeUserRole,
  changeUserStatus,
  getAuditHistory,
  createGarageAPI,
  updateGarageAPI,
  deleteGarageAPI,
  createFloorAPI,
  updateFloorAPI,
  deleteFloorAPI,
  getFloorSpaces,
  setAuthState,
  getStoredToken,
  clearAuthState,
} from './api.js';

let floorPollInterval = null;
let dashToken = null;
let dashUser = null;

// Stripe state
let stripeInstance = null;
let cardElement = null;

// Reservation data cache (for action buttons)
let _lastResRows = [];

// =============================================================
//  UTILITY
// =============================================================

function setLoading(btn, isLoading) {
  btn.disabled = isLoading;
  btn.textContent = isLoading ? 'Loading...' : (btn.dataset.label || btn.textContent);
}

function showFeedback(el, message, isError) {
  el.textContent = message;
  el.style.display = 'block';
  el.style.background = isError ? 'rgba(231,76,60,0.12)' : 'rgba(46,204,113,0.12)';
  el.style.color = isError ? 'var(--danger)' : 'var(--success)';
  el.style.border = `1px solid ${isError ? 'var(--danger)' : 'var(--success)'}`;
}

function hideFeedback(el) {
  el.style.display = 'none';
  el.textContent = '';
}

/** Combine a date string "YYYY-MM-DD" and time "HH:MM" into ISO 8601 UTC */
function toISO(date, time) {
  return new Date(`${date}T${time}:00`).toISOString();
}

function floorLabel(num) {
  if (num === -1) return 'Basement';
  return `${num}`;
}

function badgeClass(status) {
  switch (status?.toLowerCase()) {
    case 'confirmed': return 'badge-ongoing';
    case 'fulfilled': return 'badge-completed';
    case 'expired':   return 'badge-expired';
    case 'cancelled': return 'badge-cancelled';
    default:          return '';
  }
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}

//For ticket creation / error popup

function openPopup() {
  document.getElementById('ticket-popup').classList.remove('hidden');
  document.getElementById('ticket-popup-ok').onclick = () => {
    document.getElementById('ticket-popup').classList.add('hidden');
  }
}
  
function showTicketPopup(result, plate) {
  document.getElementById('ticket-popup-title').textContent = 'Ticket created!';
  document.getElementById('ticket-popup-title').classList.remove('error');
  document.getElementById('ticket-popup-details').classList.remove('hidden');
  document.getElementById('ticket-popup-error-msg').classList.add('hidden');

  document.getElementById('ticket-popup-id').textContent = result.ticketId;
  document.getElementById('ticket-popup-plate').textContent = result.licensePlate || plate;
  document.getElementById('ticket-popup-floor').textContent = floorLabel(result.assignedFloor);
  document.getElementById('ticket-popup-time').textContent = new Date(result.entryTime).toLocaleString();
  document.getElementById('ticket-popup-status').textContent = result.status;

  openPopup();
}
  
function showErrorPopup(message) {
  document.getElementById('ticket-popup-title').textContent = 'Could not create ticket';
  document.getElementById('ticket-popup-title').classList.add('error');
  document.getElementById('ticket-popup-details').classList.add('hidden');
  document.getElementById('ticket-popup-error-msg').textContent = message;
  document.getElementById('ticket-popup-error-msg').classList.remove('hidden');

  openPopup();
}

// ── Input validation helpers ──────────────────────────────────────
// Basic format checks — not exhaustive, but catch obvious typos.

// ── Modal helper (replaces prompt/confirm for production UX) ──────
function showModal(title, body, { placeholder = 'e.g. ABC-1234', validate = null } = {}) {
  return new Promise((resolve) => {
    const overlay = document.getElementById('gf-modal-overlay');
    const input   = document.getElementById('gf-modal-input');
    const errEl   = document.getElementById('gf-modal-input-error');

    document.getElementById('gf-modal-title').textContent = title;
    document.getElementById('gf-modal-body').textContent  = body;
    input.value = '';
    input.placeholder = placeholder;
    errEl.style.display = 'none';
    overlay.style.display = 'flex';
    input.focus();

    function cleanup() {
      overlay.style.display = 'none';
      document.getElementById('gf-modal-cancel').onclick = null;
      document.getElementById('gf-modal-confirm').onclick = null;
      input.onkeydown = null;
    }

    document.getElementById('gf-modal-cancel').onclick = () => { cleanup(); resolve(null); };

    function tryConfirm() {
      const val = input.value.trim();
      if (!val) { errEl.textContent = 'This field is required.'; errEl.style.display = 'block'; return; }
      if (validate && !validate(val)) { errEl.textContent = 'Invalid format.'; errEl.style.display = 'block'; return; }
      cleanup();
      resolve(val);
    }

    document.getElementById('gf-modal-confirm').onclick = tryConfirm;
    input.onkeydown = (e) => { if (e.key === 'Enter') tryConfirm(); if (e.key === 'Escape') { cleanup(); resolve(null); } };
  });
}

/** License plate: 2-10 alphanumeric chars, optional hyphens/spaces */
const PLATE_RE = /^[A-Za-z0-9][A-Za-z0-9 \-]{0,8}[A-Za-z0-9]$/;
function isValidPlate(v) { return PLATE_RE.test(v.trim()); }

/** Phone: E.164 (+1...), US 10-digit, or dashed (001-555-0000) */
const PHONE_RE = /^(\+?\d{1,3}[\-\s]?)?\(?\d{2,4}\)?[\-\s]?\d{3,4}[\-\s]?\d{3,4}$/;
function isValidPhone(v) { return PHONE_RE.test(v.trim()); }

/** Operating hours: HH:MM-HH:MM or 24/7 */
const HOURS_RE = /^(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}|24\/7)$/i;
function isValidHours(v) { return HOURS_RE.test(v.trim()); }


// =============================================================
//  GARAGE ENTRY  —  POST /v1/tickets
// =============================================================

async function handleEntryFinish() {
  const plate       = document.getElementById('entry-plate').value.trim();
  const driverClass = document.getElementById('vehicle-type').value;

  if (!plate)       { showErrorPopup('Please enter a license plate number.'); return; }
  if (!isValidPlate(plate)) { showErrorPopup('Invalid plate format. Use 2-10 letters/numbers (hyphens OK).'); return; }
  if (!driverClass) { showErrorPopup('Please select a driver class.'); return; }

  const btn = document.getElementById('btn-entry-submit');
  setLoading(btn, true);

  try {
    const result = await postTicket(plate, driverClass);

    showTicketPopup(result, plate);

    document.getElementById('entry-plate').value = '';
    document.getElementById('vehicle-type').value = '';
  } catch (err) {
    showErrorPopup(err.message);
  } finally {
    setLoading(btn, false);
  }
}


// =============================================================
//  VEHICLE EXIT  —  PUT /v1/tickets/{id}/exit + Stripe
// =============================================================

let _exitTicketData = null;  // stashed after plate lookup

async function handleExitCheckStatus() {
  const plate = document.getElementById('exit-plate').value.trim();
  if (!plate) { alert('Please enter a license plate number.'); return; }
  if (!isValidPlate(plate)) { alert('Invalid plate format. Use 2-10 letters/numbers (hyphens OK).'); return; }

  const btn = document.getElementById('btn-exit-check');
  const statusEl = document.getElementById('exit-status-result');
  const info = document.getElementById('exit-ticket-info');
  const result = document.getElementById('exit-result');

  setLoading(btn, true);
  info.style.display = 'none';
  result.style.display = 'none';
  _exitTicketData = null;

  try {
    const tickets = await getTicketsByPlate(plate);
    const active = Array.isArray(tickets) && tickets.length > 0 ? tickets[0] : null;
    if (!active) {
      showFeedback(statusEl, `No active ticket found for ${plate.toUpperCase()} — vehicle is not in the garage.`, true);
      return;
    }
    _exitTicketData = active;
    showFeedback(statusEl, `Vehicle found — Floor ${active.assignedFloor} · Entry: ${new Date(active.entryTime).toLocaleString()}`, false);
    document.getElementById('exit-ticket-id').textContent = active.ticketId;
    document.getElementById('exit-entry-time').textContent = new Date(active.entryTime).toLocaleString();
    info.style.display = 'block';
  } catch (err) {
    showFeedback(statusEl, `Lookup failed: ${err.message}`, true);
  } finally {
    setLoading(btn, false);
  }
}

async function handleExitProcess() {
  if (!_exitTicketData) { alert('Look up a ticket first.'); return; }

  const btn = document.getElementById('btn-exit-process');
  const result = document.getElementById('exit-result');
  const method = document.getElementById('exit-payment').value;
  setLoading(btn, true);
  result.style.display = 'none';

  try {
    const res = await exitTicket(_exitTicketData.ticketId, _exitTicketData.licensePlate, method);

    if (method === 'card') {
      // Show fee then attempt Stripe payment
      document.getElementById('exit-fee-display').textContent = `$${res.totalFee.toFixed(2)}`;
      document.getElementById('exit-fee-row').style.display = '';
      await handleStripePayment(res);
    } else {
      // Cash — show confirmation immediately
      showFeedback(result,
        `Exit complete. Fee: $${res.totalFee.toFixed(2)} — Cash collected.`, false);
      resetExitPanel();
    }
  } catch (err) {
    showFeedback(result, `Exit failed: ${err.message}`, true);
  } finally {
    setLoading(btn, false);
  }
}

async function handleStripePayment(exitRes) {
  const result = document.getElementById('exit-result');
  const cardWrap = document.getElementById('exit-card-wrap');

  // Initialise Stripe if needed
  if (!stripeInstance) {
    try {
      const cfg = await getPaymentConfig();
      if (!cfg.publishableKey) {
        showFeedback(result, 'Stripe is not configured. Please collect payment manually.', true);
        resetExitPanel();
        return;
      }
      stripeInstance = window.Stripe(cfg.publishableKey);
    } catch (_e) {
      showFeedback(result, 'Could not load Stripe configuration.', true);
      resetExitPanel();
      return;
    }
  }

  // Create PaymentIntent
  let intentData;
  try {
    intentData = await createPaymentIntent(exitRes.ticketId);
  } catch (err) {
    showFeedback(result, `Payment setup failed: ${err.message}`, true);
    resetExitPanel();
    return;
  }

  // Mount card element
  cardWrap.style.display = 'block';
  const elements = stripeInstance.elements();
  if (cardElement) cardElement.destroy();
  cardElement = elements.create('card', {
    style: {
      base: {
        color: '#e8eaf0',
        fontFamily: '"DM Sans", sans-serif',
        fontSize: '14px',
        '::placeholder': { color: '#5a6070' },
      },
      invalid: { color: '#e74c3c' },
    },
  });
  cardElement.mount('#exit-card-element');

  // Show pay button
  const payBtn = document.getElementById('btn-exit-pay');
  payBtn.style.display = '';
  payBtn.dataset.label = `Pay $${intentData.amount.toFixed(2)}`;
  payBtn.textContent = `Pay $${intentData.amount.toFixed(2)}`;
  document.getElementById('btn-exit-process').style.display = 'none';

  // Store intent for the pay handler
  payBtn.onclick = async () => {
    setLoading(payBtn, true);
    try {
      const { error, paymentIntent } = await stripeInstance.confirmCardPayment(
        intentData.clientSecret, { payment_method: { card: cardElement } }
      );
      if (error) {
        showFeedback(result, `Payment failed: ${error.message}`, true);
      } else if (paymentIntent.status === 'succeeded') {
        showFeedback(result,
          `Payment confirmed. Fee: $${intentData.amount.toFixed(2)} — Card charged.`, false);
        resetExitPanel();
      }
    } catch (err) {
      showFeedback(result, `Payment error: ${err.message}`, true);
    } finally {
      setLoading(payBtn, false);
    }
  };
}

function resetExitPanel() {
  _exitTicketData = null;
  document.getElementById('exit-plate').value = '';
  document.getElementById('exit-status-result').style.display = 'none';
  document.getElementById('exit-ticket-info').style.display = 'none';
  document.getElementById('exit-card-wrap').style.display = 'none';
  document.getElementById('exit-fee-row').style.display = 'none';
  const payBtn = document.getElementById('btn-exit-pay');
  payBtn.style.display = 'none';
  document.getElementById('btn-exit-process').style.display = '';
  if (cardElement) { cardElement.destroy(); cardElement = null; }
}

function handleExitPaymentChange() {
  // No pre-mount needed; Stripe card is mounted after exit is processed
}


// =============================================================
//  RESERVATION ENTRY  —  POST /v1/reservations
// =============================================================

async function handleResFinish() {
  const phone        = document.getElementById('res-phone').value.trim();
  const arrivalDate  = document.getElementById('res-arrival-date').value;
  const arrivalTime  = document.getElementById('res-arrival-time').value;
  const licensePlate = document.getElementById('res-plate').value.trim();
  const driverClass  = document.getElementById('res-driver-class').value || null;
  const feedback     = document.getElementById('res-feedback');

  hideFeedback(feedback);

  if (!phone)       { showFeedback(feedback, 'Phone number is required.', true); return; }
  if (!isValidPhone(phone)) { showFeedback(feedback, 'Invalid phone format. Use digits with optional country code (e.g. 001-555-0000).', true); return; }
  if (!arrivalDate || !arrivalTime) {
    showFeedback(feedback, 'Scheduled arrival date and time are required.', true);
    return;
  }
  if (!licensePlate) { showFeedback(feedback, 'License plate is required.', true); return; }
  if (!isValidPlate(licensePlate)) { showFeedback(feedback, 'Invalid plate format. Use 2-10 letters/numbers (hyphens OK).', true); return; }

  const scheduledArrival = toISO(arrivalDate, arrivalTime);

  const btn = document.getElementById('btn-res-submit');
  setLoading(btn, true);

  try {
    const result = await postReservation(phone, scheduledArrival, driverClass, licensePlate);
    showFeedback(
      feedback,
      `Reservation confirmed! ID: ${result.reservationId} / Floor: ${result.assignedFloor}`,
      false
    );
    document.getElementById('res-phone').value = '';
    document.getElementById('res-plate').value = '';
    document.getElementById('res-driver-class').value = '';
    setDefaultArrival();
  } catch (err) {
    showFeedback(feedback, err.message, true);
  } finally {
    setLoading(btn, false);
  }
}


// =============================================================
//  RESERVATION SEARCH  —  GET /v1/reservations
// =============================================================

async function searchRes() {
  const phone    = document.getElementById('search-val').value.trim();
  const showOld  = document.getElementById('chk-old').checked;

  if (!phone) { alert('Please enter a phone number to search.'); return; }
  if (!isValidPhone(phone)) { alert('Invalid phone format. Use digits with optional country code.'); return; }

  const btn = document.getElementById('btn-res-search');
  setLoading(btn, true);

  try {
    const results = await getReservationsByPhone(phone, showOld);
    renderTable(results);
  } catch (err) {
    alert(`Search failed: ${err.message}`);
    renderTable([]);
  } finally {
    setLoading(btn, false);
  }
}

async function showUpcoming() {
  const btn = document.getElementById('btn-upcoming');
  setLoading(btn, true);
  try {
    const results = await getUpcomingReservations(5);
    renderTable(results);
  } catch (err) {
    alert(`Could not load upcoming reservations: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}

function renderTable(rows) {
  _lastResRows = rows || [];
  const tbody = document.getElementById('res-tbody');
  if (!rows || !rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-dim); padding:20px;">No reservations found</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r, idx) => {
    const isConfirmed = r.status?.toLowerCase() === 'confirmed';
    const actions = isConfirmed
      ? `<button class="btn btn-secondary btn-sm" onclick="window._resCheckIn(${idx})">Check In</button>
         <button class="btn btn-secondary btn-sm btn-danger-text" onclick="window._resCancel(${idx})">Cancel</button>`
      : '<span style="color:var(--text-dim);">—</span>';
    return `
    <tr>
      <td>${escapeHtml(r.reservationId)}</td>
      <td>${new Date(r.scheduledArrival).toLocaleString()}</td>
      <td>${floorLabel(r.assignedFloor)}</td>
      <td><span class="badge ${badgeClass(r.status)}">${escapeHtml(r.status)}</span></td>
      <td>${actions}</td>
    </tr>`;
  }).join('');
}


// =============================================================
//  RESERVATION ACTIONS  —  Check-in / Cancel
// =============================================================

window._resCheckIn = async function(idx) {
  const r = _lastResRows[idx];
  if (!r) return;
  const plate = await showModal(
    `Check In: ${r.reservationId}`,
    'Enter the vehicle license plate to confirm check-in.',
    { validate: isValidPlate }
  );
  if (!plate) return;

  try {
    const res = await checkInReservation(r.reservationId, plate);
    alert(`Checked in! Ticket #${res.ticketId} created — Floor ${res.assignedFloor}, Spot ${res.spotId}`);
    const phone = document.getElementById('search-val').value.trim();
    if (phone) searchRes(); else showUpcoming();
  } catch (err) {
    alert(`Check-in failed: ${err.message}`);
  }
};

window._resCancel = async function(idx) {
  const r = _lastResRows[idx];
  if (!r) return;
  const plate = await showModal(
    `Cancel: ${r.reservationId}`,
    'Enter the vehicle license plate to confirm cancellation.',
    { validate: isValidPlate }
  );
  if (!plate) return;

  try {
    await cancelReservation(r.reservationId, plate, r.phone);
    alert(`Reservation ${r.reservationId} cancelled.`);
    const phone = document.getElementById('search-val').value.trim();
    if (phone) searchRes(); else showUpcoming();
  } catch (err) {
    alert(`Cancel failed: ${err.message}`);
  }
};


// =============================================================
//  FLOOR OVERVIEW
// =============================================================

async function showFloor() {
  const btn     = document.getElementById('btn-floor-show');
  const cards   = document.getElementById('floor-cards');
  const visible = btn.dataset.visible === 'true';

  if (visible) {
    if (floorPollInterval) { clearInterval(floorPollInterval); floorPollInterval = null; }
    cards.style.display = 'none';
    btn.dataset.visible = 'false';
    btn.textContent     = 'Load Floor Data';
    btn.dataset.label   = 'Load Floor Data';
    return;
  }

  setLoading(btn, true);
  try {
    await renderFloorCards();
    cards.style.display = 'grid';
    btn.dataset.visible = 'true';
    btn.textContent     = 'Hide Floor Data';
    btn.dataset.label   = 'Hide Floor Data';

    if (floorPollInterval) clearInterval(floorPollInterval);
    floorPollInterval = setInterval(async () => {
      try { await renderFloorCards(); } catch (_e) { /* skip */ }
    }, 30000);
  } catch (err) {
    alert(`Could not load floor data: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}

async function renderFloorCards() {
  const floors = await getAllFloors();
  const cards = document.getElementById('floor-cards');
  cards.innerHTML = '';
  floors.forEach(f => {
    const floorName = f.floorName || `Floor ${f.floorNumber}`;
    const occupied = f.totalSpots - f.availableSpots;
    const pct = f.totalSpots ? Math.round((occupied / f.totalSpots) * 100) : 0;
    const isFull = f.availableSpots === 0;
    cards.innerHTML += `
      <div class="zone-card">
        <div class="zone-card-title">${escapeHtml(floorName)}</div>
        <div class="zone-row"><span class="zone-key">Available</span><span class="zone-val ${isFull ? 'full' : ''}">${isFull ? 'Full' : f.availableSpots}</span></div>
        <div class="zone-row"><span class="zone-key">Total</span><span class="zone-val">${f.totalSpots}</span></div>
        <div class="zone-row"><span class="zone-key">Occupancy</span><span class="zone-val">${pct}%</span></div>
      </div>`;
  });
}


// =============================================================
//  GARAGE INFO  — populate header on load
// =============================================================

async function loadGarageName() {
  try {
    const garage = await getGarage();
    const nameEl = document.getElementById('garage-name');
    if (nameEl && garage?.name) nameEl.textContent = garage.name;
    const phoneEl = document.getElementById('garage-phone');
    if (phoneEl && garage?.frontDeskPhone) phoneEl.textContent = garage.frontDeskPhone;
  } catch (_e) {
    // Silently leave fallback values
  }
}

function setDefaultArrival() {
  const soon = new Date(Date.now() + 15 * 60 * 1000);
  const date = soon.getFullYear() + '-'
    + String(soon.getMonth() + 1).padStart(2, '0') + '-'
    + String(soon.getDate()).padStart(2, '0');
  const time = String(soon.getHours()).padStart(2, '0') + ':'
    + String(soon.getMinutes()).padStart(2, '0');
  document.getElementById('res-arrival-date').value = date;
  document.getElementById('res-arrival-time').value = time;
}


// =============================================================
//  OPERATOR DASHBOARD  —  Login / Logout / Tabs
// =============================================================

function showDashLoggedIn(user) {
  dashUser = user;
  document.getElementById('dash-login').style.display = 'none';
  document.getElementById('dash-content').style.display = 'block';
  document.getElementById('dash-username').textContent = `Logged in as ${user.username} (${user.role})`;
  document.getElementById('dash-user-info').style.display = '';
  switchTab('tab-analytics');
  refreshDashboard();
}

function showDashLoggedOut() {
  if (!dashToken && !dashUser) return; // guard re-entry from clearAuthState callback
  dashToken = null;
  dashUser = null;
  clearAuthState();
  document.getElementById('dash-login').style.display = '';
  document.getElementById('dash-content').style.display = 'none';
  document.getElementById('dash-user-info').style.display = 'none';
  document.getElementById('dash-user').value = '';
  document.getElementById('dash-pass').value = '';
}

async function dashLogin() {
  const user = document.getElementById('dash-user').value.trim();
  const pass = document.getElementById('dash-pass').value.trim();
  if (!user || !pass) { alert('Enter username and password.'); return; }
  const btn = document.getElementById('btn-dash-login');
  setLoading(btn, true);
  try {
    const res = await loginStaff(user, pass);
    dashToken = res.token;
    setAuthState(res.token, res.expiresAt, showDashLoggedOut);
    showDashLoggedIn(res.user);
  } catch (err) {
    alert('Login failed: ' + err.message);
  } finally {
    setLoading(btn, false);
  }
}

async function dashLogout() {
  try { await logoutStaff(); } catch (_e) { /* ok */ }
  showDashLoggedOut();
}


// =============================================================
//  DASHBOARD TABS
// =============================================================

function switchTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const target = document.getElementById(tabId);
  if (target) target.style.display = 'block';
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  if (btn) btn.classList.add('active');

  // Lazy-load tab data
  if (tabId === 'tab-staff') loadStaffList();
  if (tabId === 'tab-audit') loadAuditHistory(1);
  if (tabId === 'tab-config') loadGarageConfig();
}


// =============================================================
//  DASHBOARD — Analytics (existing)
// =============================================================

async function refreshDashboard() {
  if (!dashToken) return;
  const now = new Date();
  const weekAgo = new Date(now - 7 * 24 * 60 * 60 * 1000);
  const from = weekAgo.toISOString();
  const to = now.toISOString();

  try {
    const occ = await getOccupancy(dashToken);
	document.getElementById('dash-occupied').textContent = occ.live.occupied;
	document.getElementById('dash-available').textContent = occ.live.available;
	document.getElementById('dash-rate').textContent = Math.round(occ.live.utilizationRate) + '%';
  } catch (_e) {
    document.getElementById('dash-occupied').textContent = 'Error';
  }

  try {
    const rev = await getRevenue(dashToken, from, to);
    document.getElementById('dash-revenue').textContent = '$' + rev.totalRevenue.toFixed(2);
  } catch (_e) {
    document.getElementById('dash-revenue').textContent = 'Error';
  }

  try {
    const occ = await getOccupancy(dashToken);
	const utilEl = document.getElementById('dash-util');
	const last7 = (occ.trend || []).slice(-7);
	utilEl.innerHTML = last7.slice().reverse().map(d =>
	`<div style="display:flex; justify-content:space-between; padding:2px 0; border-bottom:1px solid var(--border);">` +
	`<span>${d.date}</span><span>${d.occupied} occupied (${d.utilizationRate}%)</span></div>`
	).join('') || 'No data for this period';
	} catch (_e) {
		document.getElementById('dash-util').textContent = 'Could not load utilization data.';
  }

  try {
	  const peak = await getPeakHours(dashToken, from, to);
	  const peakEl = document.getElementById('dash-peak');
	  peakEl.innerHTML = peak.hours.filter(h => h.totalEntries > 0).slice(0, 8).map(h =>
	  `<div style="display:flex; justify-content:space-between; padding:2px 0; border-bottom:1px solid var(--border);">` +
	  `<span>${String(h.hour).padStart(2,'0')}:00</span><span>${h.totalEntries} entries</span></div>`
	  ).join('') || 'No data';
  } catch (_e) {
    document.getElementById('dash-peak').textContent = 'Could not load peak hours.';
  }
}


// =============================================================
//  DASHBOARD — Staff Management
// =============================================================

async function loadStaffList() {
  const container = document.getElementById('staff-list');
  container.innerHTML = '<div style="color:var(--text-muted);">Loading...</div>';
  try {
    const users = await getUsers();
    if (!users.length) { container.innerHTML = 'No staff accounts.'; return; }
    container.innerHTML = `
      <table><thead><tr>
        <th>ID</th><th>Name</th><th>Username</th><th>Role</th><th>Active</th><th>Actions</th>
      </tr></thead><tbody>
      ${users.map(u => `
        <tr>
          <td>${u.operatorId}</td>
          <td>${escapeHtml(u.name)}</td>
          <td>${escapeHtml(u.username)}</td>
          <td>
            <select class="staff-role-select" data-uid="${u.operatorId}" style="background:var(--input-bg);border:1px solid var(--border);color:var(--text);padding:4px;border-radius:4px;font-size:12px;">
              <option value="admin" ${u.role==='admin'?'selected':''}>admin</option>
              <option value="attendant" ${u.role==='attendant'?'selected':''}>attendant</option>
              <option value="manager" ${u.role==='manager'?'selected':''}>manager</option>
            </select>
          </td>
          <td>
            <button class="btn btn-secondary btn-sm staff-status-btn"
              data-uid="${u.operatorId}" data-active="${u.isActive}"
              style="font-size:11px;padding:3px 10px;">
              ${u.isActive ? 'Deactivate' : 'Activate'}
            </button>
          </td>
          <td style="font-size:11px;color:var(--text-muted);">${u.isActive ? 'Active' : 'Inactive'}</td>
        </tr>`).join('')}
      </tbody></table>`;

    // Wire role selects
    container.querySelectorAll('.staff-role-select').forEach(sel => {
      sel.addEventListener('change', async (e) => {
        const uid = e.target.dataset.uid;
        try {
          await changeUserRole(uid, e.target.value);
        } catch (err) { alert(`Role change failed: ${err.message}`); loadStaffList(); }
      });
    });

    // Wire status buttons
    container.querySelectorAll('.staff-status-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const uid = btn.dataset.uid;
        const newStatus = btn.dataset.active === 'true' ? 'deactivated' : 'active';
        try {
          await changeUserStatus(uid, newStatus);
          loadStaffList();
        } catch (err) { alert(`Status change failed: ${err.message}`); }
      });
    });
  } catch (err) {
    container.innerHTML = `<div style="color:var(--danger);">Failed to load staff: ${err.message}</div>`;
  }
}

async function handleCreateStaff() {
  const name = document.getElementById('staff-new-name').value.trim();
  const username = document.getElementById('staff-new-username').value.trim();
  const password = document.getElementById('staff-new-password').value.trim();
  const role = document.getElementById('staff-new-role').value;

  if (!name || !username || !password) { alert('All fields are required.'); return; }

  const btn = document.getElementById('btn-staff-create');
  setLoading(btn, true);
  try {
    await registerStaff({ name, username, password, role });
    document.getElementById('staff-new-name').value = '';
    document.getElementById('staff-new-username').value = '';
    document.getElementById('staff-new-password').value = '';
    loadStaffList();
  } catch (err) {
    alert(`Create failed: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}


// =============================================================
//  DASHBOARD — Audit History
// =============================================================

let _auditPage = 1;
const _AUDIT_LIMIT = 50;

async function loadAuditHistory(page = 1) {
  _auditPage = page;
  const container = document.getElementById('audit-list');
  const actionFilter = document.getElementById('audit-action').value.trim();
  const fromDate = document.getElementById('audit-from')?.value || '';
  const toDate = document.getElementById('audit-to')?.value || '';
  container.innerHTML = '<div style="color:var(--text-muted);">Loading...</div>';

  try {
    const params = { page, limit: _AUDIT_LIMIT };
    if (actionFilter) params.action = actionFilter;
    if (fromDate) params.from = new Date(fromDate).toISOString();
    if (toDate) params.to = new Date(toDate + 'T23:59:59').toISOString();

    const data = await getAuditHistory(params);
    // Support both old (array) and new (paginated object) response shapes
    const events = Array.isArray(data) ? data : (data.events || []);
    const total = data.total || events.length;
    const totalPages = Math.ceil(total / _AUDIT_LIMIT) || 1;

    if (!events.length) { container.innerHTML = 'No audit events found.'; return; }
    const rows = events.map(e => `
        <tr>
          <td>${e.eventId}</td>
          <td>${escapeHtml(e.source)}</td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(e.description)}</td>
          <td>${e.staffId || '—'}</td>
          <td>${e.createdAt ? new Date(e.createdAt).toLocaleString() : '—'}</td>
        </tr>`).join('');

    const prevDisabled = page <= 1 ? 'disabled' : '';
    const nextDisabled = page >= totalPages ? 'disabled' : '';
    container.innerHTML = `
      <table><thead><tr>
        <th>ID</th><th>Source</th><th>Description</th><th>Staff ID</th><th>Time</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 0; font-size:13px; color:var(--text-muted);">
        <span>Page ${page} of ${totalPages} (${total} total)</span>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary btn-sm" id="audit-prev" ${prevDisabled}>Prev</button>
          <button class="btn btn-secondary btn-sm" id="audit-next" ${nextDisabled}>Next</button>
        </div>
      </div>`;

    document.getElementById('audit-prev')?.addEventListener('click', () => loadAuditHistory(page - 1));
    document.getElementById('audit-next')?.addEventListener('click', () => loadAuditHistory(page + 1));
  } catch (err) {
    container.innerHTML = `<div style="color:var(--danger);">Failed to load audit log: ${err.message}</div>`;
  }
}


// =============================================================
//  DASHBOARD — Ticket Overrides (Reopen Cash Tickets)
// =============================================================

async function searchClosedTickets() {
  const container = document.getElementById('override-results');
  const query = document.getElementById('override-search').value.trim();
  if (!query) { container.innerHTML = '<div style="color:var(--text-muted);">Enter a plate or ticket ID to search.</div>'; return; }

  container.innerHTML = '<div style="color:var(--text-muted);">Searching...</div>';
  try {
    const tickets = await getClosedTickets();
    const matches = (tickets || []).filter(t => {
      const q = query.toLowerCase();
      return String(t.ticketId) === query || (t.licensePlate && t.licensePlate.toLowerCase().includes(q));
    });

    if (!matches.length) { container.innerHTML = '<div style="color:var(--text-muted);">No closed tickets found.</div>'; return; }

    const rows = matches.map(t => {
      const exit = t.exitTime ? new Date(t.exitTime).toLocaleString() : '—';
      const isCash = t.paymentMethod === 'cash';
      const reopenBtn = isCash
        ? `<button class="btn btn-primary btn-sm" data-action="reopen-ticket" data-id="${t.ticketId}">Reopen</button>`
        : `<span style="color:var(--text-muted); font-size:12px;">${escapeHtml(t.paymentMethod || 'n/a')}</span>`;
      return `<tr>
        <td>${t.ticketId}</td>
        <td>${escapeHtml(t.licensePlate || '—')}</td>
        <td>Floor ${t.assignedFloor || '—'}</td>
        <td>${exit}</td>
        <td>$${t.totalFee != null ? Number(t.totalFee).toFixed(2) : '—'}</td>
        <td>${reopenBtn}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <table><thead><tr>
        <th>ID</th><th>Plate</th><th>Floor</th><th>Exit Time</th><th>Fee</th><th>Action</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } catch (err) {
    container.innerHTML = `<div style="color:var(--danger);">Failed to search: ${err.message}</div>`;
  }
}

async function handleReopenTicket(ticketId) {
  if (!confirm(`Reopen ticket #${ticketId}? This will reverse the cash payment and re-activate the ticket.`)) return;
  const container = document.getElementById('override-results');
  try {
    await overrideTicket(ticketId, { action: 'reopen', reason: 'Operator correction via admin panel' });
    showToast(`Ticket #${ticketId} reopened successfully.`, 'success');
    searchClosedTickets();
  } catch (err) {
    showToast(`Failed to reopen: ${err.message}`, 'error');
  }
}


// =============================================================
//  DASHBOARD — Garage Config
// =============================================================

let _editingGarageId = null;
let _currentGarage = null;

async function loadGarageConfig() {
  const currentSection = document.getElementById('cfg-current');
  try {
    const data = await getGarage();
    const g = Array.isArray(data) ? data[0] : data;
    if (!g) { currentSection.style.display = 'none'; _currentGarage = null; return; }
    _currentGarage = g;
    currentSection.style.display = 'block';
    document.getElementById('cfg-current-info').innerHTML = `
      <div class="zone-row"><span class="zone-key">Name</span><span class="zone-val">${escapeHtml(g.name)}</span></div>
      <div class="zone-row"><span class="zone-key">ID</span><span class="zone-val">${g.garageId}</span></div>
      <div class="zone-row"><span class="zone-key">Total Capacity</span><span class="zone-val">${g.totalCapacity}</span></div>
      <div class="zone-row"><span class="zone-key">Floors</span><span class="zone-val">${g.numberOfFloors}</span></div>
      <div class="zone-row"><span class="zone-key">Operating Hours</span><span class="zone-val">${escapeHtml(g.operatingHours)}</span></div>
      <div class="zone-row"><span class="zone-key">Phone</span><span class="zone-val">${escapeHtml(g.frontDeskPhone || '—')}</span></div>`;
    currentSection.dataset.garageId = g.garageId;
    loadFloorConfig(g.garageId);
  } catch (_e) {
    currentSection.style.display = 'none';
    _currentGarage = null;
    document.getElementById('cfg-floors-section').style.display = 'none';
  }
}

function startEditGarage() {
  if (!_currentGarage) return;
  _editingGarageId = _currentGarage.garageId;

  document.getElementById('cfg-name').value = _currentGarage.name || '';
  document.getElementById('cfg-hours').value = _currentGarage.operatingHours || '';
  document.getElementById('cfg-phone').value = _currentGarage.frontDeskPhone || '';

  document.getElementById('cfg-form-title').textContent = 'Edit Garage';
  document.getElementById('btn-cfg-create').textContent = 'Save Changes';
  document.getElementById('btn-cfg-create').dataset.label = 'Save Changes';
  document.getElementById('btn-cfg-cancel-edit').style.display = '';
}

function cancelEditGarage() {
  _editingGarageId = null;
  document.getElementById('cfg-form-title').textContent = 'Create Garage';
  document.getElementById('btn-cfg-create').textContent = 'Create Garage';
  document.getElementById('btn-cfg-create').dataset.label = 'Create Garage';
  document.getElementById('btn-cfg-cancel-edit').style.display = 'none';
  document.getElementById('cfg-name').value = '';
  document.getElementById('cfg-hours').value = '';
  document.getElementById('cfg-phone').value = '';
}

async function handleDeleteGarage() {
  const currentSection = document.getElementById('cfg-current');
  const garageId = currentSection.dataset.garageId;
  if (!garageId) return;
  if (!confirm('Delete this garage? This will cascade to all floors and spots.')) return;

  const result = document.getElementById('cfg-result');
  try {
    await deleteGarageAPI(garageId);
    showFeedback(result, 'Garage deleted.', false);
    currentSection.style.display = 'none';
    cancelEditGarage();
    loadGarageName();
  } catch (err) {
    showFeedback(result, `Delete failed: ${err.message}`, true);
  }
}

async function handleCreateGarage() {
  const name = document.getElementById('cfg-name').value.trim();
  const hours = document.getElementById('cfg-hours').value.trim();
  const phone = document.getElementById('cfg-phone').value.trim();

  if (!name || !hours) { alert('Garage name and operating hours are required.'); return; }
  if (!isValidHours(hours)) { alert('Invalid hours format. Use HH:MM-HH:MM or 24/7.'); return; }

  const btn = document.getElementById('btn-cfg-create');
  setLoading(btn, true);
  const result = document.getElementById('cfg-result');
  result.style.display = 'none';
  try {
    const payload = { name, operatingHours: hours, frontDeskPhone: phone || null };
    let g;
    if (_editingGarageId) {
      g = await updateGarageAPI(_editingGarageId, payload);
      showFeedback(result, `Garage "${g.name}" updated.`, false);
      cancelEditGarage();
    } else {
      // Placeholder values — _sync_garage() recalculates once floors are added
      payload.totalCapacity = 1;
      payload.numberOfFloors = 1;
      g = await createGarageAPI(payload);
      showFeedback(result, `Garage "${g.name}" created. Add floors below.`, false);
    }
    document.getElementById('cfg-name').value = '';
    document.getElementById('cfg-hours').value = '';
    document.getElementById('cfg-phone').value = '';
    loadGarageConfig();
    loadGarageName();
  } catch (err) {
    showFeedback(result, `Failed: ${err.message}`, true);
  } finally {
    setLoading(btn, false);
  }
}


// =============================================================
//  FLOOR MANAGEMENT — config tab
// =============================================================

const SPOT_TYPES = ['standard', 'accessibility', 'staff', 'eco'];

async function loadFloorConfig(garageId) {
  const section = document.getElementById('cfg-floors-section');
  if (!garageId) { section.style.display = 'none'; return; }
  section.style.display = 'block';

  try {
    const floors = await getAllFloors(garageId);
    const container = document.getElementById('cfg-floors-list');

    if (!floors.length) {
      container.innerHTML = '<div style="color:var(--muted); padding:8px 0; font-size:13px;">No floors configured yet. Add one below.</div>';
      return;
    }

    // Fetch spot details for each floor in parallel
    const spotsPerFloor = await Promise.all(floors.map(f => getFloorSpaces(f.floorId).catch(() => [])));

    container.innerHTML = '';
    floors.sort((a, b) => a.floorNumber - b.floorNumber);
    floors.forEach((f, i) => {
      const spots = spotsPerFloor[i] || [];
      const occupied = f.totalSpots - f.availableSpots;
      const pct = f.totalSpots ? Math.round((occupied / f.totalSpots) * 100) : 0;
      const typeCounts = {};
      SPOT_TYPES.forEach(t => { typeCounts[t] = 0; });
      spots.forEach(s => { if (typeCounts[s.spotType] !== undefined) typeCounts[s.spotType]++; });

      const hasOccupied = occupied > 0;

      container.innerHTML += `
        <div class="zone-card" style="margin-bottom:10px;" data-floor-id="${f.floorId}">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div class="zone-card-title">${escapeHtml(f.floorName || 'Floor ' + f.floorNumber)}</div>
            <div style="display:flex; gap:4px;">
              <button class="btn btn-secondary btn-sm" data-action="edit-floor" data-floor-id="${f.floorId}"
                      data-floor-name="${escapeHtml(f.floorName || '')}" data-floor-number="${f.floorNumber}"
                      data-total-spots="${f.totalSpots}"
                      data-type-counts='${JSON.stringify(typeCounts)}'>Edit</button>
              <button class="btn btn-secondary btn-sm btn-danger-text" data-action="delete-floor"
                      data-floor-id="${f.floorId}" ${hasOccupied ? 'disabled title="Has occupied spots"' : ''}>Delete</button>
            </div>
          </div>
          <div class="zone-row"><span class="zone-key">Floor #</span><span class="zone-val">${f.floorNumber}</span></div>
          <div class="zone-row"><span class="zone-key">Spots</span><span class="zone-val">${occupied} / ${f.totalSpots} occupied (${pct}%)</span></div>
        </div>`;
    });
  } catch (err) {
    document.getElementById('cfg-floors-list').innerHTML =
      `<div style="color:var(--danger); font-size:13px;">Failed to load floors: ${escapeHtml(err.message)}</div>`;
  }
}

let _editingFloorId = null;

function _readFloorForm() {
  return {
    name: document.getElementById('cfg-floor-name').value.trim(),
    number: parseInt(document.getElementById('cfg-floor-number').value, 10),
    spots: {
      standard: parseInt(document.getElementById('cfg-floor-standard').value, 10) || 0,
      accessibility: parseInt(document.getElementById('cfg-floor-accessibility').value, 10) || 0,
      staff: parseInt(document.getElementById('cfg-floor-staff').value, 10) || 0,
      eco: parseInt(document.getElementById('cfg-floor-eco').value, 10) || 0,
    },
  };
}

function _clearFloorForm() {
  document.getElementById('cfg-floor-name').value = '';
  document.getElementById('cfg-floor-number').value = '';
  SPOT_TYPES.forEach(t => { document.getElementById(`cfg-floor-${t}`).value = '0'; });
  _editingFloorId = null;
  document.getElementById('cfg-floor-form-title').textContent = 'Add Floor';
  document.getElementById('btn-cfg-add-floor').textContent = 'Add Floor';
  document.getElementById('btn-cfg-add-floor').dataset.label = 'Add Floor';
  document.getElementById('btn-cfg-cancel-floor').style.display = 'none';
}

async function handleAddFloor() {
  if (!_currentGarage) { alert('Create a garage first.'); return; }
  const { name, number, spots } = _readFloorForm();
  const total = spots.standard + spots.accessibility + spots.staff + spots.eco;
  const result = document.getElementById('cfg-floor-result');

  if (isNaN(number)) {
    showFeedback(result, 'Floor # is required.', true);
    return;
  }
  if (total < 1) {
    showFeedback(result, 'At least one spot is required.', true);
    return;
  }

  try {
    if (_editingFloorId) {
      const payload = { floorName: name || null, spots };
      await updateFloorAPI(_editingFloorId, payload);
      showFeedback(result, 'Floor updated.', false);
      _clearFloorForm();
    } else {
      await createFloorAPI({
        garageId: _currentGarage.garageId,
        floorNumber: number,
        floorName: name || null,
        spots,
      });
      showFeedback(result, `Floor ${number} added.`, false);
      _clearFloorForm();
    }
    await loadGarageConfig();
  } catch (err) {
    showFeedback(result, `Failed: ${err.message}`, true);
  }
}

function startEditFloor(floorId, floorName, floorNumber, typeCounts) {
  _editingFloorId = floorId;
  document.getElementById('cfg-floor-name').value = floorName || '';
  document.getElementById('cfg-floor-number').value = floorNumber;
  document.getElementById('cfg-floor-number').disabled = true;
  SPOT_TYPES.forEach(t => {
    document.getElementById(`cfg-floor-${t}`).value = typeCounts[t] || 0;
  });
  document.getElementById('cfg-floor-form-title').textContent = `Edit Floor ${floorNumber}`;
  document.getElementById('btn-cfg-add-floor').textContent = 'Update Floor';
  document.getElementById('btn-cfg-add-floor').dataset.label = 'Update Floor';
  document.getElementById('btn-cfg-cancel-floor').style.display = '';
  document.getElementById('cfg-floor-form').scrollIntoView({ behavior: 'smooth' });
}

function cancelEditFloor() {
  document.getElementById('cfg-floor-number').disabled = false;
  _clearFloorForm();
}

async function handleDeleteFloor(floorId) {
  if (!confirm('Delete this floor and all its spots?')) return;
  const result = document.getElementById('cfg-floor-result');
  try {
    await deleteFloorAPI(floorId);
    showFeedback(result, 'Floor deleted.', false);
    await loadGarageConfig();
  } catch (err) {
    showFeedback(result, `Delete failed: ${err.message}`, true);
  }
}

function setupFloorConfigListeners() {
  document.getElementById('btn-cfg-add-floor').addEventListener('click', handleAddFloor);
  document.getElementById('btn-cfg-cancel-floor').addEventListener('click', () => {
    document.getElementById('cfg-floor-number').disabled = false;
    cancelEditFloor();
  });

  document.getElementById('cfg-floors-list').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const floorId = btn.dataset.floorId;

    if (action === 'edit-floor') {
      const typeCounts = JSON.parse(btn.dataset.typeCounts || '{}');
      startEditFloor(floorId, btn.dataset.floorName, parseInt(btn.dataset.floorNumber), typeCounts);
    } else if (action === 'delete-floor') {
      handleDeleteFloor(floorId);
    }
  });
}


// =============================================================
//  CONGESTION ALERT — polls /v1/capacity/alert
// =============================================================

async function checkCongestion() {
  try {
    const data = await getCapacityAlert();
    const banner = document.getElementById('congestion-banner');
    if (data.alert) {
      banner.textContent = `Garage at ${Math.round(data.occupancyRate * 100)}% capacity — ${data.available} spots remaining`;
      banner.style.display = 'block';
    } else {
      banner.style.display = 'none';
    }
  } catch (_e) { /* silently skip */ }
}


// =============================================================
//  WIRE UP  — runs once DOM is ready
// =============================================================

document.addEventListener('DOMContentLoaded', () => {
  loadGarageName();
  setDefaultArrival();
  checkCongestion();
  setInterval(checkCongestion, 30000);

  // Store original labels so setLoading() can restore them
  document.querySelectorAll('.btn').forEach(btn => {
    if (!btn.dataset.label) btn.dataset.label = btn.textContent.trim();
  });

  // Entry
  document.getElementById('btn-entry-submit')
    .addEventListener('click', handleEntryFinish);
  document.getElementById('btn-conf-dismiss').addEventListener('click', () => {
    document.getElementById('entry-confirmation').style.display = 'none';
  });

  // Exit
  document.getElementById('btn-exit-check')
    .addEventListener('click', handleExitCheckStatus);
  document.getElementById('exit-plate')
    .addEventListener('keydown', e => { if (e.key === 'Enter') handleExitCheckStatus(); });
  document.getElementById('btn-exit-process')
    .addEventListener('click', handleExitProcess);
  document.getElementById('exit-payment')
    .addEventListener('change', handleExitPaymentChange);

  // Reservations
  document.getElementById('btn-res-submit')
    .addEventListener('click', handleResFinish);
  document.getElementById('btn-res-search')
    .addEventListener('click', searchRes);
  document.getElementById('search-val')
    .addEventListener('keydown', e => { if (e.key === 'Enter') searchRes(); });
  document.getElementById('btn-floor-show')
    .addEventListener('click', showFloor);
  document.getElementById('btn-upcoming')
    .addEventListener('click', showUpcoming);

  // Dashboard auth
  document.getElementById('btn-dash-login').addEventListener('click', dashLogin);
  document.getElementById('btn-dash-logout').addEventListener('click', dashLogout);
  document.getElementById('btn-dash-refresh').addEventListener('click', refreshDashboard);

  // Dashboard tabs
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Staff create
  document.getElementById('btn-staff-create')
    .addEventListener('click', handleCreateStaff);

  // Ticket overrides
  document.getElementById('btn-override-search')
    .addEventListener('click', searchClosedTickets);
  document.getElementById('override-results')
    .addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action="reopen-ticket"]');
      if (btn) handleReopenTicket(parseInt(btn.dataset.id, 10));
    });

  // Audit filter
  document.getElementById('btn-audit-filter')
    .addEventListener('click', loadAuditHistory);

  // Garage config
  document.getElementById('btn-cfg-create')
    .addEventListener('click', handleCreateGarage);
  document.getElementById('btn-cfg-edit')
    .addEventListener('click', startEditGarage);
  document.getElementById('btn-cfg-delete')
    .addEventListener('click', handleDeleteGarage);
  document.getElementById('btn-cfg-cancel-edit')
    .addEventListener('click', cancelEditGarage);

  // Floor config
  setupFloorConfigListeners();
});
