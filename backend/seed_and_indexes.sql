-- ============================================================================
-- GarageFlow: Seed Data & Indexes
-- Database: MySQL / MariaDB
-- Generated: 2026-04-09
--
-- Usage:
--   1. Run STEP 1 pre-checks and evaluate output.
--   2. Skip or run STEP 2 inserts based on pre-check results.
--   3. Skip or run STEP 3 indexes based on pre-check results.
--   4. Run STEP 4 verification queries to confirm integrity.
-- ============================================================================


-- ============================================================================
-- STEP 1: PRE-CHECKS
-- ============================================================================

-- 1a. Check row counts in all tables
SELECT 'garage'        AS tbl, COUNT(*) AS row_count FROM garage
UNION ALL SELECT 'floor',        COUNT(*) FROM floor
UNION ALL SELECT 'parking_spot', COUNT(*) FROM parking_spot
UNION ALL SELECT 'gate_event',   COUNT(*) FROM gate_event
UNION ALL SELECT 'customer',     COUNT(*) FROM customer
UNION ALL SELECT 'vehicle',      COUNT(*) FROM vehicle
UNION ALL SELECT 'staff',        COUNT(*) FROM staff
UNION ALL SELECT 'ticket',       COUNT(*) FROM ticket
UNION ALL SELECT 'payment',      COUNT(*) FROM payment
UNION ALL SELECT 'reservation',  COUNT(*) FROM reservation
UNION ALL SELECT 'occupancy_log',COUNT(*) FROM occupancy_log
UNION ALL SELECT 'pricing_rule', COUNT(*) FROM pricing_rule
UNION ALL SELECT 'system_event', COUNT(*) FROM system_event;

-- 1b. Check which indexes already exist
SELECT INDEX_NAME, TABLE_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND INDEX_NAME IN (
    'idx_floor_garage_id',
    'idx_parking_spot_floor_id',
    'idx_vehicle_customer_id',
    'idx_ticket_vehicle_id',
    'idx_ticket_spot_id',
    'idx_ticket_entry_gate_id',
    'idx_ticket_exit_gate_id',
    'idx_reservation_customer_id',
    'idx_reservation_vehicle_id',
    'idx_occupancy_log_spot_id'
  );


-- ============================================================================
-- STEP 2: INSERTS  (Task 9 — Seed Data)
-- ============================================================================
-- Insert only into tables that are empty per Step 1 results.
-- FK dependency order: garage → floor → parking_spot, gate_event, customer →
-- vehicle, staff, pricing_rule → ticket → payment, reservation,
-- occupancy_log, system_event.
-- ============================================================================

START TRANSACTION;

-- ---------- garage ----------
INSERT INTO garage (garage_id, name, total_capacity, number_of_floors, operating_hours, front_desk_phone)
VALUES (1, 'Downtown GarageFlow Garage', 90, 3, '6:00 AM - 12:00 AM', '555-0199');

-- ---------- floor ----------
-- Floor 1: 25 standard + 5 accessibility = 30 spots, 1 occupied → 29 available
-- Floor 2: 25 standard + 5 staff        = 30 spots, 1 occupied → 29 available
-- Floor 3: 30 standard                  = 30 spots, 1 occupied → 29 available
INSERT INTO floor (floor_id, garage_id, floor_number, floor_name, total_spots, available_spots)
VALUES
  (1, 1, 1, 'Level 1', 30, 29),
  (2, 1, 2, 'Level 2', 30, 29),
  (3, 1, 3, 'Level 3', 30, 29);

-- ---------- parking_spot ----------
-- Floor 1: spots 1-25 standard, 26-30 accessibility
INSERT INTO parking_spot (spot_id, floor_id, spot_type, status, location_reference) VALUES
  ( 1, 1, 'standard', 'occupied',  '1-A01'),
  ( 2, 1, 'standard', 'available', '1-A02'),
  ( 3, 1, 'standard', 'available', '1-A03'),
  ( 4, 1, 'standard', 'available', '1-A04'),
  ( 5, 1, 'standard', 'available', '1-A05'),
  ( 6, 1, 'standard', 'available', '1-A06'),
  ( 7, 1, 'standard', 'available', '1-A07'),
  ( 8, 1, 'standard', 'available', '1-A08'),
  ( 9, 1, 'standard', 'available', '1-A09'),
  (10, 1, 'standard', 'available', '1-A10'),
  (11, 1, 'standard', 'available', '1-A11'),
  (12, 1, 'standard', 'available', '1-A12'),
  (13, 1, 'standard', 'available', '1-A13'),
  (14, 1, 'standard', 'available', '1-A14'),
  (15, 1, 'standard', 'available', '1-A15'),
  (16, 1, 'standard', 'available', '1-A16'),
  (17, 1, 'standard', 'available', '1-A17'),
  (18, 1, 'standard', 'available', '1-A18'),
  (19, 1, 'standard', 'available', '1-A19'),
  (20, 1, 'standard', 'available', '1-A20'),
  (21, 1, 'standard', 'available', '1-A21'),
  (22, 1, 'standard', 'available', '1-A22'),
  (23, 1, 'standard', 'available', '1-A23'),
  (24, 1, 'standard', 'available', '1-A24'),
  (25, 1, 'standard', 'available', '1-A25'),
  (26, 1, 'accessibility', 'available', '1-H01'),
  (27, 1, 'accessibility', 'available', '1-H02'),
  (28, 1, 'accessibility', 'available', '1-H03'),
  (29, 1, 'accessibility', 'available', '1-H04'),
  (30, 1, 'accessibility', 'available', '1-H05');

-- Floor 2: spots 31-55 standard, 56-60 staff
INSERT INTO parking_spot (spot_id, floor_id, spot_type, status, location_reference) VALUES
  (31, 2, 'standard', 'occupied',  '2-A01'),
  (32, 2, 'standard', 'available', '2-A02'),
  (33, 2, 'standard', 'available', '2-A03'),
  (34, 2, 'standard', 'available', '2-A04'),
  (35, 2, 'standard', 'available', '2-A05'),
  (36, 2, 'standard', 'available', '2-A06'),
  (37, 2, 'standard', 'available', '2-A07'),
  (38, 2, 'standard', 'available', '2-A08'),
  (39, 2, 'standard', 'available', '2-A09'),
  (40, 2, 'standard', 'available', '2-A10'),
  (41, 2, 'standard', 'available', '2-A11'),
  (42, 2, 'standard', 'available', '2-A12'),
  (43, 2, 'standard', 'available', '2-A13'),
  (44, 2, 'standard', 'available', '2-A14'),
  (45, 2, 'standard', 'available', '2-A15'),
  (46, 2, 'standard', 'available', '2-A16'),
  (47, 2, 'standard', 'available', '2-A17'),
  (48, 2, 'standard', 'available', '2-A18'),
  (49, 2, 'standard', 'available', '2-A19'),
  (50, 2, 'standard', 'available', '2-A20'),
  (51, 2, 'standard', 'available', '2-A21'),
  (52, 2, 'standard', 'available', '2-A22'),
  (53, 2, 'standard', 'available', '2-A23'),
  (54, 2, 'standard', 'available', '2-A24'),
  (55, 2, 'standard', 'available', '2-A25'),
  (56, 2, 'staff', 'available', '2-S01'),
  (57, 2, 'staff', 'available', '2-S02'),
  (58, 2, 'staff', 'available', '2-S03'),
  (59, 2, 'staff', 'available', '2-S04'),
  (60, 2, 'staff', 'available', '2-S05');

-- Floor 3: spots 61-90 all standard
INSERT INTO parking_spot (spot_id, floor_id, spot_type, status, location_reference) VALUES
  (61, 3, 'standard', 'occupied',  '3-A01'),
  (62, 3, 'standard', 'available', '3-A02'),
  (63, 3, 'standard', 'available', '3-A03'),
  (64, 3, 'standard', 'available', '3-A04'),
  (65, 3, 'standard', 'available', '3-A05'),
  (66, 3, 'standard', 'available', '3-A06'),
  (67, 3, 'standard', 'available', '3-A07'),
  (68, 3, 'standard', 'available', '3-A08'),
  (69, 3, 'standard', 'available', '3-A09'),
  (70, 3, 'standard', 'available', '3-A10'),
  (71, 3, 'standard', 'available', '3-A11'),
  (72, 3, 'standard', 'available', '3-A12'),
  (73, 3, 'standard', 'available', '3-A13'),
  (74, 3, 'standard', 'available', '3-A14'),
  (75, 3, 'standard', 'available', '3-A15'),
  (76, 3, 'standard', 'available', '3-A16'),
  (77, 3, 'standard', 'available', '3-A17'),
  (78, 3, 'standard', 'available', '3-A18'),
  (79, 3, 'standard', 'available', '3-A19'),
  (80, 3, 'standard', 'available', '3-A20'),
  (81, 3, 'standard', 'available', '3-A21'),
  (82, 3, 'standard', 'available', '3-A22'),
  (83, 3, 'standard', 'available', '3-A23'),
  (84, 3, 'standard', 'available', '3-A24'),
  (85, 3, 'standard', 'available', '3-A25'),
  (86, 3, 'standard', 'available', '3-A26'),
  (87, 3, 'standard', 'available', '3-A27'),
  (88, 3, 'standard', 'available', '3-A28'),
  (89, 3, 'standard', 'available', '3-A29'),
  (90, 3, 'standard', 'available', '3-A30');

-- ---------- gate_event ----------
INSERT INTO gate_event (gate_id, garage_id, gate_type, status) VALUES
  (1, 1, 'entry', 'open'),
  (2, 1, 'exit',  'open');

-- ---------- customer ----------
INSERT INTO customer (customer_id, name, email, phone_number, date_created, account_status) VALUES
  (1, 'Maria Santos',  'maria.santos@example.com',  '555-0101', '2026-03-01 09:00:00', 'active'),
  (2, 'James Chen',    'james.chen@example.com',     '555-0102', '2026-03-15 14:30:00', 'active');

-- ---------- vehicle ----------
-- Vehicles 1-2 belong to customers; vehicle 3 is a guest (no customer_id)
INSERT INTO vehicle (vehicle_id, license_plate, plate_state, vehicle_type, customer_id) VALUES
  (1, 'ABC-1234', 'California',  'car',        1),
  (2, 'XYZ-5678', 'New York',    'car',        2),
  (3, 'GUE-9999', 'Texas',       'truck',      NULL);

-- ---------- staff ----------
-- Passwords generated with werkzeug.security.generate_password_hash()
-- admin / admin  |  attendant1 / attendant123
INSERT INTO staff (operator_id, name, role, username, password_hash, is_active) VALUES
  (1, 'Admin User',     'admin',     'admin',
   'scrypt:32768:8:1$MuASDXPvAs8lo6Xh$86e1439bd5e835fa586098f639d4067d9f36978f0ed800a82f24ee2cf434a5abb005fc929f87c406078c7dc368260bcd7f19bbac412d49c77d59244ffd99bcf6',
   1),
  (2, 'Sam Attendant',  'attendant', 'attendant1',
   'scrypt:32768:8:1$Vfa8oNXA6APGN4GW$b8286700bb4cde2663a872882490ca61a38391f10bb7f0c2e5775cad4c0cf252b4164110d5524404c0d21f67757663c60378a1946dc94e97bed6fa25db9fdf42',
   1);

-- ---------- pricing_rule ----------
INSERT INTO pricing_rule (rate_id, rate_name, applicable_hours, pricing_model, description, program) VALUES
  (1, 'Standard Hourly',   '6:00-24:00',  'hourly',  '$5 base + $2/hr',                    NULL),
  (2, 'Flat Evening',      '18:00-24:00', 'flat',    '$10 flat rate for evening parking',   NULL),
  (3, 'Weekend Special',   '00:00-24:00', 'special', '$15 all-day weekend rate',            'weekend-promo');

-- ---------- ticket ----------
-- Tickets 1-3: ACTIVE (currently parked, no exit yet)
-- Tickets 4-6: CLOSED (exited, have duration and fee)
-- Fee formula: $5 base + $2 * ceil(minutes / 60)
INSERT INTO ticket (ticket_id, entry_timestamp, exit_timestamp, entry_gate_id, exit_gate_id,
                    spot_id, vehicle_id, status, duration, total_fee, phone) VALUES
  -- Active tickets (parked now)
  (1, '2026-04-09 07:30:00', NULL, 1, NULL,  1, 1, 'active', NULL, NULL,   '555-0101'),
  (2, '2026-04-09 08:15:00', NULL, 1, NULL, 31, 2, 'active', NULL, NULL,   '555-0102'),
  (3, '2026-04-09 09:00:00', NULL, 1, NULL, 61, 3, 'active', NULL, NULL,   NULL),
  -- Closed tickets (historical)
  (4, '2026-04-08 10:00:00', '2026-04-08 12:30:00', 1, 2,  3, 1, 'closed', 150, 11.00, '555-0101'),
  (5, '2026-04-07 14:00:00', '2026-04-07 16:00:00', 1, 2, 33, 2, 'closed', 120,  9.00, '555-0102'),
  (6, '2026-04-06 08:00:00', '2026-04-06 17:15:00', 1, 2, 62, 3, 'closed', 555, 25.00,  NULL);

-- ---------- payment ----------
-- One payment per closed ticket (tickets 4, 5, 6)
INSERT INTO payment (payment_id, ticket_id, amount_charged, payment_method,
                     payment_timestamp, payment_status, stripe_payment_intent_id) VALUES
  (1, 4, 11.00, 'card',   '2026-04-08 12:31:00', 'paid', 'pi_test_00000000001'),
  (2, 5,  9.00, 'cash',   '2026-04-07 16:01:00', 'paid',  NULL),
  (3, 6, 25.00, 'mobile', '2026-04-06 17:16:00', 'paid', 'pi_test_00000000002');

-- ---------- reservation ----------
-- 1 confirmed (upcoming), 1 fulfilled (converted to ticket), 1 expired
INSERT INTO reservation (reservation_id, customer_id, vehicle_id, phone, driver_class,
                         floor_number, start_datetime, end_datetime, status,
                         created_at, quoted_fee) VALUES
  (1, 1, 1, '555-0101', 'standard',      1, '2026-04-10 10:00:00', '2026-04-10 14:00:00',
      'confirmed', '2026-04-08 20:00:00', 13.00),
  (2, 2, 2, '555-0102', 'standard',      2, '2026-04-07 14:00:00', '2026-04-07 16:00:00',
      'fulfilled', '2026-04-06 18:00:00',  9.00),
  (3, 1, 1, '555-0101', 'accessibility', 1, '2026-04-05 09:00:00', '2026-04-05 11:00:00',
      'expired',   '2026-04-04 15:00:00',  9.00);

-- ---------- occupancy_log ----------
-- Entry ('occupied') and exit ('freed') events matching ticket activity
INSERT INTO occupancy_log (log_id, spot_id, changed_at, change_type) VALUES
  -- Active ticket 1: entered spot 1
  ( 1,  1, '2026-04-09 07:30:00', 'occupied'),
  -- Active ticket 2: entered spot 31
  ( 2, 31, '2026-04-09 08:15:00', 'occupied'),
  -- Active ticket 3: entered spot 61
  ( 3, 61, '2026-04-09 09:00:00', 'occupied'),
  -- Closed ticket 4: spot 3 enter + exit
  ( 4,  3, '2026-04-08 10:00:00', 'occupied'),
  ( 5,  3, '2026-04-08 12:30:00', 'freed'),
  -- Closed ticket 5: spot 33 enter + exit
  ( 6, 33, '2026-04-07 14:00:00', 'occupied'),
  ( 7, 33, '2026-04-07 16:00:00', 'freed'),
  -- Closed ticket 6: spot 62 enter + exit
  ( 8, 62, '2026-04-06 08:00:00', 'occupied'),
  ( 9, 62, '2026-04-06 17:15:00', 'freed'),
  -- Additional historical activity
  (10,  5, '2026-04-05 11:00:00', 'occupied'),
  (11,  5, '2026-04-05 15:30:00', 'freed'),
  (12, 40, '2026-04-04 09:00:00', 'occupied'),
  (13, 40, '2026-04-04 13:00:00', 'freed');

-- ---------- system_event ----------
INSERT INTO system_event (event_id, staff_id, source, description, created_at) VALUES
  (1, 1, 'seed_script',  'Database seeded with initial data.',                  '2026-04-09 06:00:00'),
  (2, 2, 'gate_control', 'Entry gate 1 opened for morning shift.',             '2026-04-09 06:00:00');

COMMIT;


-- ============================================================================
-- STEP 3: INDEXES  (Task 10 — Create Missing Indexes)
-- ============================================================================
-- Run only for indexes NOT found in Step 1 pre-check.
-- MySQL does not support CREATE INDEX IF NOT EXISTS, so each must be evaluated
-- individually against the Step 1 results.
-- ============================================================================

CREATE INDEX idx_floor_garage_id         ON floor (garage_id);
CREATE INDEX idx_parking_spot_floor_id   ON parking_spot (floor_id);
CREATE INDEX idx_vehicle_customer_id     ON vehicle (customer_id);
CREATE INDEX idx_ticket_vehicle_id       ON ticket (vehicle_id);
CREATE INDEX idx_ticket_spot_id          ON ticket (spot_id);
CREATE INDEX idx_ticket_entry_gate_id    ON ticket (entry_gate_id);
CREATE INDEX idx_ticket_exit_gate_id     ON ticket (exit_gate_id);
CREATE INDEX idx_reservation_customer_id ON reservation (customer_id);
CREATE INDEX idx_reservation_vehicle_id  ON reservation (vehicle_id);
CREATE INDEX idx_occupancy_log_spot_id   ON occupancy_log (spot_id);


-- ============================================================================
-- STEP 4: VERIFICATION
-- ============================================================================

-- 4a. Total spots per floor matches floor.total_spots
SELECT f.floor_id, f.floor_name, f.total_spots,
       COUNT(ps.spot_id) AS actual_spot_count,
       CASE WHEN f.total_spots = COUNT(ps.spot_id) THEN 'PASS' ELSE 'FAIL' END AS check_result
FROM floor f
LEFT JOIN parking_spot ps ON ps.floor_id = f.floor_id
GROUP BY f.floor_id, f.floor_name, f.total_spots;

-- 4b. available_spots equals COUNT of spots with status='available'
SELECT f.floor_id, f.floor_name, f.available_spots,
       SUM(CASE WHEN ps.status = 'available' THEN 1 ELSE 0 END) AS actual_available,
       CASE WHEN f.available_spots = SUM(CASE WHEN ps.status = 'available' THEN 1 ELSE 0 END)
            THEN 'PASS' ELSE 'FAIL' END AS check_result
FROM floor f
LEFT JOIN parking_spot ps ON ps.floor_id = f.floor_id
GROUP BY f.floor_id, f.floor_name, f.available_spots;

-- 4c. All active tickets have exit_timestamp IS NULL
SELECT ticket_id, status, exit_timestamp,
       CASE WHEN exit_timestamp IS NULL THEN 'PASS' ELSE 'FAIL' END AS check_result
FROM ticket
WHERE status = 'active';

-- 4d. All closed tickets have a matching payment row
SELECT t.ticket_id, t.status,
       CASE WHEN p.payment_id IS NOT NULL THEN 'PASS' ELSE 'FAIL' END AS check_result
FROM ticket t
LEFT JOIN payment p ON p.ticket_id = t.ticket_id
WHERE t.status = 'closed';

-- 4e. All reservations reference valid customer_id and vehicle_id
SELECT r.reservation_id,
       CASE WHEN c.customer_id IS NOT NULL THEN 'PASS' ELSE 'FAIL' END AS customer_check,
       CASE WHEN v.vehicle_id IS NOT NULL THEN 'PASS' ELSE 'FAIL' END AS vehicle_check
FROM reservation r
LEFT JOIN customer c ON c.customer_id = r.customer_id
LEFT JOIN vehicle v  ON v.vehicle_id  = r.vehicle_id;

-- 4f. All occupancy_log entries reference valid spot_id
SELECT ol.log_id,
       CASE WHEN ps.spot_id IS NOT NULL THEN 'PASS' ELSE 'FAIL' END AS spot_check
FROM occupancy_log ol
LEFT JOIN parking_spot ps ON ps.spot_id = ol.spot_id;

-- 4g. Confirm all 10 indexes exist
SELECT INDEX_NAME, TABLE_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND INDEX_NAME IN (
    'idx_floor_garage_id',
    'idx_parking_spot_floor_id',
    'idx_vehicle_customer_id',
    'idx_ticket_vehicle_id',
    'idx_ticket_spot_id',
    'idx_ticket_entry_gate_id',
    'idx_ticket_exit_gate_id',
    'idx_reservation_customer_id',
    'idx_reservation_vehicle_id',
    'idx_occupancy_log_spot_id'
  )
ORDER BY TABLE_NAME, INDEX_NAME;
