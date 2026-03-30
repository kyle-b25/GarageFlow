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
    case 'fulfilled': return 'badge-completed';
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
  const phone        = document.getElementById('res-phone').value.trim();
  const arrivalDate  = document.getElementById('res-arrival-date').value;
  const arrivalTime  = document.getElementById('res-arrival-time').value;
  const licensePlate = document.getElementById('res-plate').value.trim();
  const driverClass  = document.getElementById('res-driver-class').value || null;
  const feedback     = document.getElementById('res-feedback');

  hideFeedback(feedback);

  if (!phone)       { showFeedback(feedback, '⚠️ Phone number is required.', true); return; }
  if (!arrivalDate || !arrivalTime) {
    showFeedback(feedback, '⚠️ Scheduled arrival date and time are required.', true);
    return;
  }
  if (!licensePlate) { showFeedback(feedback, '⚠️ License plate is required.', true); return; }

  const scheduledArrival = toISO(arrivalDate, arrivalTime);

  const btn = document.getElementById('btn-res-submit');
  setLoading(btn, true);

  try {
    const result = await postReservation(phone, scheduledArrival, driverClass, licensePlate);
    showFeedback(
      feedback,
      `✅ Reservation confirmed! ID: ${result.reservationId} · Floor: ${result.assignedFloor}`,
      false
    );
    // Clear form
    document.getElementById('res-phone').value = '';
    document.getElementById('res-plate').value = '';
    document.getElementById('res-driver-class').value = '';
    setDefaultArrival();
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
  const btn     = document.getElementById('btn-floor-show');
  const cards   = document.getElementById('floor-cards');
  const visible = btn.dataset.visible === 'true';

  // If data is showing, hide it and reset
  if (visible) {
    cards.style.display = 'none';
    btn.dataset.visible = 'false';
    btn.textContent     = 'Load Floor Data';
    return;
  }

  // Otherwise load (or just re-show if already loaded)
  setLoading(btn, true);
  try {
    const floors = await getAllFloors();
    cards.innerHTML = '';
    floors.forEach(f => {
      const floorName = f.floorName || `Floor ${f.floorNumber}`;
      const occupied = f.totalSpots - f.availableSpots;
      const pct = f.totalSpots ? Math.round((occupied / f.totalSpots) * 100) : 0;
      const isFull = f.availableSpots === 0;

      cards.innerHTML += `
        <div class="zone-card">
          <div class="zone-card-title">${floorName}</div>
          <div class="zone-row">
            <span class="zone-key">Available</span>
            <span class="zone-val ${isFull ? 'full' : ''}">${isFull ? 'Full' : f.availableSpots}</span>
          </div>
          <div class="zone-row">
            <span class="zone-key">Total</span>
            <span class="zone-val">${f.totalSpots}</span>
          </div>
          <div class="zone-row">
            <span class="zone-key">Occupancy</span>
            <span class="zone-val">${pct}%</span>
          </div>
        </div>
      `;
    });

    cards.style.display = 'grid';
    btn.dataset.visible = 'true';
    btn.textContent     = 'Hide Floor Data';
	btn.dataset.label   = 'Hide Floor Data';
  } catch (err) {
    alert(`Could not load floor data: ${err.message}`);
  } finally {
    setLoading(btn, false);
  }
}

// =============================================================
//  GARAGE INFO  — populate header on load
//  Sam Gibney 3/27/2026
// =============================================================

async function loadGarageName() {
  try {
    const garage = await getGarage();

    const nameEl = document.getElementById('garage-name');
    if (nameEl && garage?.name) nameEl.textContent = garage.name;

    const phoneEl = document.getElementById('garage-phone');
    if (phoneEl && garage?.frontDeskPhone) phoneEl.textContent = garage.frontDeskPhone;
  } catch (_) {
    // Silently leave fallback values if the API call fails
  }
}

/** Pre-fill the scheduled arrival fields to 15 minutes from now. */
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
//  WIRE UP  — runs once DOM is ready
// =============================================================

document.addEventListener('DOMContentLoaded', () => {
	loadGarageName();
	setDefaultArrival();

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
	
  document.getElementById('btn-upcoming')
    .addEventListener('click', showUpcoming);

});
