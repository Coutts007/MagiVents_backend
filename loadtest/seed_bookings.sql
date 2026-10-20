-- 100,000 confirmed bookings spread over every event, so the database looks like a site that has been selling for a while
INSERT INTO tickets_booking (id, gathering_id, quantity, admits_per_ticket, tier_name, unit_price, is_sale_price, total_price, total_in_kes, currency, discount, attendee_name, attendee_email, status, payment_method, mpesa_phone_number, mpesa_receipt_number, mpesa_mode, notes, booking_date, confirmed_at, holds_tickets, confirmation_email_due)
SELECT gen_random_uuid(), g.ids[1 + (n % array_length(g.ids, 1))], 1 + (n % 3), 1, 'Standard', 1000, false, 1000 * (1 + (n % 3)), 1000 * (1 + (n % 3)), 'KES', 0, 'Seed ' || n, 'seed' || n || '@load.test', 'confirmed', 'mpesa', '', '', '', '', now(), now(), true, false
FROM generate_series(1, 100000) n, (SELECT array_agg(id) ids FROM gatherings_gathering) g;
ANALYZE;
