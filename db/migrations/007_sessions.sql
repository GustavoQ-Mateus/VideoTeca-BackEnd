ALTER TABLE events ADD COLUMN IF NOT EXISTS room_id BIGINT REFERENCES rooms(id);
ALTER TABLE events ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ;
DO $$ BEGIN
 ALTER TABLE events ADD CONSTRAINT events_room_period CHECK(room_id IS NULL OR (starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at>starts_at AND film_id IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
