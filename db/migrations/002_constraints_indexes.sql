-- Videoteca Unifor — Migration 002
-- Constraints de integridade (CHECK de status e validações) + índices de performance.
-- Idempotente: pode ser reaplicada com segurança.

-- ── CHECK de status (domínios válidos) ─────────────────────────────
DO $$ BEGIN
  ALTER TABLE films ADD CONSTRAINT films_status_chk
    CHECK (status IN ('available', 'inactive', 'maintenance'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE copies ADD CONSTRAINT copies_status_chk
    CHECK (status IN ('available', 'reserved', 'borrowed', 'maintenance', 'damaged'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE reservations ADD CONSTRAINT reservations_status_chk
    CHECK (status IN ('waiting', 'ready', 'fulfilled', 'cancelled', 'expired'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE loans ADD CONSTRAINT loans_status_chk
    CHECK (status IN ('borrowed', 'returned', 'late'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE rooms ADD CONSTRAINT rooms_status_chk
    CHECK (status IN ('available', 'inactive', 'maintenance'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE room_bookings ADD CONSTRAINT room_bookings_status_chk
    CHECK (status IN ('pending', 'confirmed', 'cancelled'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE events ADD CONSTRAINT events_status_chk
    CHECK (status IN ('draft', 'published', 'cancelled', 'finished'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE professor_requests ADD CONSTRAINT professor_requests_status_chk
    CHECK (status IN ('analysis', 'approved', 'refused'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Validações de dados ────────────────────────────────────────────
DO $$ BEGIN
  ALTER TABLE room_bookings ADD CONSTRAINT room_bookings_period_chk
    CHECK (ends_at > starts_at);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE rooms ADD CONSTRAINT rooms_capacity_chk
    CHECK (capacity IS NULL OR capacity > 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE films ADD CONSTRAINT films_year_chk
    CHECK (year IS NULL OR year BETWEEN 1888 AND 2100);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE events ADD CONSTRAINT events_slots_chk
    CHECK (slots >= 0 AND enrolled >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE loans ADD CONSTRAINT loans_return_chk
    CHECK (returned_at IS NULL OR returned_at >= borrowed_at);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Índices de performance (FKs e colunas de consulta) ─────────────
CREATE INDEX IF NOT EXISTS idx_profiles_role            ON profiles (role);
CREATE INDEX IF NOT EXISTS idx_films_status             ON films (status);
CREATE INDEX IF NOT EXISTS idx_films_genre              ON films (genre);
CREATE INDEX IF NOT EXISTS idx_copies_film_id           ON copies (film_id);
CREATE INDEX IF NOT EXISTS idx_copies_status            ON copies (status);
CREATE INDEX IF NOT EXISTS idx_reservations_user_id     ON reservations (user_id);
CREATE INDEX IF NOT EXISTS idx_reservations_film_id     ON reservations (film_id);
CREATE INDEX IF NOT EXISTS idx_reservations_status      ON reservations (status);
CREATE INDEX IF NOT EXISTS idx_loans_user_id            ON loans (user_id);
CREATE INDEX IF NOT EXISTS idx_loans_copy_id            ON loans (copy_id);
CREATE INDEX IF NOT EXISTS idx_loans_status             ON loans (status);
CREATE INDEX IF NOT EXISTS idx_loans_due_at             ON loans (due_at);
CREATE INDEX IF NOT EXISTS idx_room_bookings_room_id    ON room_bookings (room_id);
CREATE INDEX IF NOT EXISTS idx_room_bookings_prof_id    ON room_bookings (professor_id);
CREATE INDEX IF NOT EXISTS idx_room_bookings_starts_at  ON room_bookings (starts_at);
CREATE INDEX IF NOT EXISTS idx_events_film_id           ON events (film_id);
CREATE INDEX IF NOT EXISTS idx_events_starts_at         ON events (starts_at);
CREATE INDEX IF NOT EXISTS idx_events_status            ON events (status);
CREATE INDEX IF NOT EXISTS idx_event_regs_user_id       ON event_registrations (user_id);
CREATE INDEX IF NOT EXISTS idx_prof_requests_prof_id    ON professor_requests (professor_id);
CREATE INDEX IF NOT EXISTS idx_prof_requests_status     ON professor_requests (status);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id    ON notifications (user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read       ON notifications (read);
