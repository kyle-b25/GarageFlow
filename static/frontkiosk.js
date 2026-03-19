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
} from './api.js';


// =============================================================
//  UTILITY
// =============================================================

function setLoading(btn, isLoading) {
  btn.disabled = isLoading;
  btn.textContent = isLoading ? 'Loading…' : btn.dataset.label;
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
    case 'complete':  return 'badge-completed';
    case 'expired':   return 'badge-expired';
    default:          return '';
  }
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
    alert(`✅ Ticket created!\nTicket ID: ${result.ticketId}\nAssigned Floor: ${floorLabel(result.assignedFloor)}\nEntry Time: ${new Date(result.entryTime).toLocaleString()}`);
    document.getElementById('entry-plate').value = '';
    document.getElementById('vehicle-type').value = '';
  } catch (err) {
    alert(`⚠️ Could not create ticket: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}


// =============================================================
//  RESERVATION ENTRY  —  POST /v1/reservations
// =============================================================

async function handleResFinish() {
  const phone       = document.getElementById('res-phone').value.trim();
  const arrivalDate = document.getElementById('res-arrival-date').value;
  const arrivalTime = document.getElementById('res-arrival-time').value;
  const driverClass = document.getElementById('res-driver-class').value || null;
  const feedback    = document.getElementById('res-feedback');

  hideFeedback(feedback);

  if (!phone)       { showFeedback(feedback, '⚠️ Phone number is required.', true); return; }
  if (!arrivalDate || !arrivalTime) {
    showFeedback(feedback, '⚠️ Scheduled arrival date and time are required.', true);
    return;
  }

  const scheduledArrival = toISO(arrivalDate, arrivalTime);

  const btn = document.getElementById('btn-res-submit');
  setLoading(btn, true);

  try {
    const result = await postReservation(phone, scheduledArrival, driverClass);
    showFeedback(
      feedback,
      `✅ Reservation confirmed! ID: ${result.reservationId} · Floor: ${floorLabel(result.assignedFloor)}`,
      false
    );
    // Clear form
    document.getElementById('res-phone').value = '';
    document.getElementById('res-arrival-date').value = '';
    document.getElementById('res-arrival-time').value = '';
    document.getElementById('res-driver-class').value = '';
  } catch (err) {
    showFeedback(feedback, `⚠️ ${err.message}`, true);
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

function renderTable(rows) {
  const tbody = document.getElementById('res-tbody');
  if (!rows || !rows.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-dim); padding:20px;">No reservations found</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${r.reservationId}</td>
      <td>${new Date(r.scheduledArrival).toLocaleString()}</td>
      <td>${floorLabel(r.assignedFloor)}</td>
      <td><span class="badge ${badgeClass(r.status)}">${r.status}</span></td>
    </tr>
  `).join('');
}


// =============================================================
//  FLOOR OVERVIEW
// =============================================================

async function showFloor() {
  const btn = document.getElementById('btn-floor-show');
  setLoading(btn, true);

  try {
    const floors = await getAllFloors();
    const f2 = floors.find(f => f.floor === 'Floor 2');
    const f3 = floors.find(f => f.floor === 'Floor 3');
    if (f2) populateZoneCard('f2', f2.total, f2.zones);
    if (f3) populateZoneCard('f3', f3.total, f3.zones);
  } catch (err) {
    alert(`Could not load floor data: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}

function populateZoneCard(prefix, total, zones) {
  document.getElementById(`${prefix}-total`).textContent = total ?? '—';
  setZoneVal(`${prefix}-a`, zones?.A ?? '—');
  setZoneVal(`${prefix}-b`, zones?.B ?? '—');
  setZoneVal(`${prefix}-c`, zones?.C ?? '—');
  setZoneVal(`${prefix}-d`, zones?.D ?? '—');
}

function setZoneVal(id, val) {
  const el = document.getElementById(id);
  el.textContent = val;
  el.className = 'zone-val' + (val === 'Full' ? ' full' : '');
}


// =============================================================
//  WIRE UP  — runs once DOM is ready
// =============================================================

document.addEventListener('DOMContentLoaded', () => {

  // Store original labels so setLoading() can restore them
  document.querySelectorAll('.btn').forEach(btn => {
    btn.dataset.label = btn.textContent.trim();
  });

  document.getElementById('btn-entry-submit')
    .addEventListener('click', handleEntryFinish);

  document.getElementById('btn-res-submit')
    .addEventListener('click', handleResFinish);

  document.getElementById('btn-res-search')
    .addEventListener('click', searchRes);

  document.getElementById('search-val')
    .addEventListener('keydown', e => { if (e.key === 'Enter') searchRes(); });

  document.getElementById('btn-floor-show')
    .addEventListener('click', showFloor);

});
