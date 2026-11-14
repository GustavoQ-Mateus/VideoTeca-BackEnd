ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS course TEXT;
ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS registration TEXT;
ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS student_count INT CHECK (student_count > 0);
ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE professor_requests ADD COLUMN IF NOT EXISTS booking_id BIGINT UNIQUE REFERENCES room_bookings(id);
