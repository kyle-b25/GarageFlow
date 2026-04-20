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


// =============================================================
//  GARAGE ENTRY  —  POST /v1/tickets
// =============================================================

async function handleEntryFinish() {
  const plate       = document.getElementById('entry-plate').value.trim();
  const driverClass = document.getElementById('vehicle-type').value;

  if (!plate)       { alert('Please enter a license plate number.'); return; }
  if (!driverClass) { alert('Please select a driver class.'); return; }

  const btn = document.getElementById('btn-entry-submit');
  setLoading(btn, true);

  try {
    const result = await postTicket(plate, driverClass);
    document.getElementById('conf-ticket-id').textContent = result.ticketId;
    document.getElementById('conf-plate').textContent = result.licensePlate || plate;
    document.getElementById('conf-floor').textContent = floorLabel(result.assignedFloor);
    document.getElementById('conf-time').textContent = new Date(result.entryTime).toLocaleString();
    document.getElementById('conf-status').textContent = result.status;
    document.getElementById('entry-confirmation').style.display = 'block';
    document.getElementById('entry-plate').value = '';
    document.getElementById('vehicle-type').value = '';
  } catch (err) {
    alert(`Could not create ticket: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}


// =============================================================
//  VEHICLE EXIT  —  PUT /v1/tickets/{id}/exit + Stripe
// =============================================================

let _exitTicketData = null;  // stashed after lookup

async function handleExitLookup() {
  const plate = document.getElementById('exit-plate').value.trim();
  if (!plate) { alert('Please enter a license plate number.'); return; }

  const btn = document.getElementById('btn-exit-lookup');
  setLoading(btn, true);
  const info = document.getElementById('exit-ticket-info');
  const result = document.getElementById('exit-result');
  info.style.display = 'none';
  result.style.display = 'none';

  try {
    const tickets = await getTicketsByPlate(plate);
    if (!tickets || tickets.length === 0) {
      showFeedback(result, 'No active ticket found for this plate.', true);
      _exitTicketData = null;
      return;
    }
    const t = tickets[0];
    _exitTicketData = t;
    document.getElementById('exit-ticket-id').textContent = t.ticketId;
    document.getElementById('exit-entry-time').textContent = new Date(t.entryTime).toLocaleString();
    info.style.display = 'block';
  } catch (err) {
    showFeedback(result, `Lookup failed: ${err.message}`, true);
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
  if (!arrivalDate || !arrivalTime) {
    showFeedback(feedback, 'Scheduled arrival date and time are required.', true);
    return;
  }
  if (!licensePlate) { showFeedback(feedback, 'License plate is required.', true); return; }

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
  const plate = prompt(`Check-in reservation ${r.reservationId}\nEnter the vehicle license plate to confirm:`);
  if (!plate) return;

  try {
    const res = await checkInReservation(r.reservationId, plate.trim());
    alert(`Checked in! Ticket #${res.ticketId} created — Floor ${res.assignedFloor}, Spot ${res.spotId}`);
    // Refresh table
    const phone = document.getElementById('search-val').value.trim();
    if (phone) searchRes(); else showUpcoming();
  } catch (err) {
    alert(`Check-in failed: ${err.message}`);
  }
};

window._resCancel = async function(idx) {
  const r = _lastResRows[idx];
  if (!r) return;
  if (!confirm(`Cancel reservation ${r.reservationId}?`)) return;
  const plate = prompt('Enter the vehicle license plate to confirm cancellation:');
  if (!plate) return;

  try {
    await cancelReservation(r.reservationId, plate.trim(), r.phone);
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
  dashToken = null;
  dashUser = null;
  clearAuthState();
  document.getElementById('dash-login').style.display = '';
  document.getElementById('dash-content').style.display = 'none';
  document.getElementById('dash-user-info').style.display = 'none';
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
  if (tabId === 'tab-audit') loadAuditHistory();
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
    document.getElementById('dash-occupied').textContent = occ.occupiedSpots;
    document.getElementById('dash-available').textContent = occ.availableSpots;
    document.getElementById('dash-rate').textContent = Math.round(occ.occupancyRate * 100) + '%';
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
    const util = await getUtilization(dashToken, from, to);
    const utilEl = document.getElementById('dash-util');
    utilEl.innerHTML = util.days.map(d =>
      `<div style="display:flex; justify-content:space-between; padding:2px 0; border-bottom:1px solid var(--border);">` +
      `<span>${d.date}</span><span>${d.entries} in / ${d.exits} out</span></div>`
    ).join('');
  } catch (_e) {
    document.getElementById('dash-util').textContent = 'Could not load utilization data.';
  }

  try {
    const peak = await getPeakHours(dashToken, from, to);
    const peakEl = document.getElementById('dash-peak');
    const sorted = peak.hours.filter(h => h.count > 0).sort((a, b) => b.count - a.count).slice(0, 8);
    peakEl.innerHTML = sorted.map(h =>
      `<div style="display:flex; justify-content:space-between; padding:2px 0; border-bottom:1px solid var(--border);">` +
      `<span>${String(h.hour).padStart(2,'0')}:00</span><span>${h.count} entries</span></div>`
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

async function loadAuditHistory() {
  const container = document.getElementById('audit-list');
  const actionFilter = document.getElementById('audit-action').value.trim();
  container.innerHTML = '<div style="color:var(--text-muted);">Loading...</div>';

  try {
    const params = {};
    if (actionFilter) params.action = actionFilter;
    const events = await getAuditHistory(params);
    if (!events.length) { container.innerHTML = 'No audit events found.'; return; }
    container.innerHTML = `
      <table><thead><tr>
        <th>ID</th><th>Source</th><th>Description</th><th>Staff ID</th><th>Time</th>
      </tr></thead><tbody>
      ${events.slice(0, 100).map(e => `
        <tr>
          <td>${e.eventId}</td>
          <td>${escapeHtml(e.source)}</td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(e.description)}</td>
          <td>${e.staffId || '—'}</td>
          <td>${e.createdAt ? new Date(e.createdAt).toLocaleString() : '—'}</td>
        </tr>`).join('')}
      </tbody></table>`;
  } catch (err) {
    container.innerHTML = `<div style="color:var(--danger);">Failed to load audit log: ${err.message}</div>`;
  }
}


// =============================================================
//  DASHBOARD — Garage Config
// =============================================================

async function handleCreateGarage() {
  const name = document.getElementById('cfg-name').value.trim();
  const capacity = parseInt(document.getElementById('cfg-capacity').value, 10);
  const floors = parseInt(document.getElementById('cfg-floors').value, 10);
  const hours = document.getElementById('cfg-hours').value.trim();

  if (!name || !capacity || !floors || !hours) { alert('All fields are required.'); return; }

  const btn = document.getElementById('btn-cfg-create');
  setLoading(btn, true);
  const result = document.getElementById('cfg-result');
  result.style.display = 'none';
  try {
    const g = await createGarageAPI({
      name, totalCapacity: capacity, numberOfFloors: floors, operatingHours: hours,
    });
    showFeedback(result, `Garage "${g.name}" created (ID: ${g.garageId}).`, false);
    document.getElementById('cfg-name').value = '';
    document.getElementById('cfg-capacity').value = '';
    document.getElementById('cfg-floors').value = '';
    document.getElementById('cfg-hours').value = '';
  } catch (err) {
    showFeedback(result, `Failed: ${err.message}`, true);
  } finally {
    setLoading(btn, false);
  }
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
  document.getElementById('btn-exit-lookup')
    .addEventListener('click', handleExitLookup);
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

  // Audit filter
  document.getElementById('btn-audit-filter')
    .addEventListener('click', loadAuditHistory);

  // Garage config
  document.getElementById('btn-cfg-create')
    .addEventListener('click', handleCreateGarage);
});
