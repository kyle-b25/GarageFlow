-- Task 11: Test all database operations

START TRANSACTION;

-- Insert test data
INSERT INTO garage (name, total_capacity, number_of_floors, operating_hours, front_desk_phone)
VALUES ('GarageFlow Central', 120, 2, '6:00am-midnight', '555-0100');

INSERT INTO floor (garage_id, floor_number, floor_name, total_spots, available_spots)
VALUES (1, 0, 'Ground', 60, 60);

INSERT INTO parking_spot (floor_id, spot_type, status, location_reference)
VALUES (1, 'standard', 'available', 'A-1');

INSERT INTO gate_event (garage_id, gate_type, status)
VALUES (1, 'entry', 'closed');

INSERT INTO customer (name, email, phone_number, date_created, account_status)
VALUES ('Alex Rivera', 'alex@example.com', '555-1001', CURRENT_TIMESTAMP, 'active');

INSERT INTO vehicle (license_plate, plate_state, vehicle_type, customer_id)
VALUES ('ABC123', 'NY', 'car', 1);

INSERT INTO staff (name, role, username, password_hash)
VALUES ('Sam Operator', 'attendant', 'soperator', 'hashed_password_here');

INSERT INTO reservation (customer_id, vehicle_id, start_datetime, end_datetime, status, created_at, quoted_fee)
VALUES (1, 1, '2026-03-18 10:00:00', '2026-03-18 12:00:00', 'confirmed', CURRENT_TIMESTAMP, 10.00);

INSERT INTO ticket (entry_timestamp, exit_timestamp, entry_gate_id, exit_gate_id, spot_id, vehicle_id, status, duration, total_fee)
VALUES ('2026-03-18 10:05:00', NULL, 1, NULL, 1, 1, 'active', NULL, NULL);

INSERT INTO payment (ticket_id, amount_charged, payment_method, payment_timestamp, payment_status)
VALUES (1, 10.00, 'card', CURRENT_TIMESTAMP, 'paid');

INSERT INTO occupancy_log (spot_id, changed_at, change_type)
VALUES (1, CURRENT_TIMESTAMP, 'occupied');

INSERT INTO system_event (source, description)
VALUES ('ticket_module', 'Ticket created successfully');

INSERT INTO pricing_rule (rate_name, applicable_hours, pricing_model, description, program)
VALUES ('standard', 'all day', 'hourly', 'Standard hourly rate', 'calculate_standard_fee');

-- Read test
SELECT * FROM garage;
SELECT * FROM floor;
SELECT * FROM parking_spot;
SELECT * FROM gate_event;
SELECT * FROM customer;
SELECT * FROM vehicle;
SELECT * FROM staff;
SELECT * FROM reservation;
SELECT * FROM ticket;
SELECT * FROM payment;
SELECT * FROM occupancy_log;
SELECT * FROM system_event;
SELECT * FROM pricing_rule;

-- Update test
UPDATE parking_spot
SET status = 'occupied'
WHERE spot_id = 1;

UPDATE floor
SET available_spots = available_spots - 1
WHERE floor_id = 1;

UPDATE ticket
SET status = 'closed',
    exit_timestamp = '2026-03-18 11:45:00',
    duration = 100,
    total_fee = 10.00,
    exit_gate_id = 1
WHERE ticket_id = 1;

-- Delete test
DELETE FROM payment WHERE payment_id = 1;
DELETE FROM ticket WHERE ticket_id = 1;
DELETE FROM reservation WHERE reservation_id = 1;

-- Cleanup changes
ROLLBACK;
