-- A single exclusion constraint serializes sessions and reservations, including concurrent writes.
ALTER TABLE room_bookings ADD COLUMN IF NOT EXISTS event_id BIGINT UNIQUE REFERENCES events(id) ON DELETE CASCADE;
CREATE OR REPLACE FUNCTION sync_session_occupancy() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE selected_room rooms%ROWTYPE;
BEGIN
 IF NEW.room_id IS NOT NULL AND NEW.status='published' THEN
   SELECT * INTO selected_room FROM rooms WHERE id=NEW.room_id FOR UPDATE;
   IF NOT FOUND OR selected_room.status<>'available' OR NEW.slots>selected_room.capacity THEN
     RAISE EXCEPTION 'Sala indisponivel ou capacidade excedida' USING ERRCODE='23514';
   END IF;
   PERFORM id FROM films WHERE id=NEW.film_id AND status<>'inactive' FOR UPDATE;
   IF NOT FOUND THEN
     RAISE EXCEPTION 'Filme indisponivel' USING ERRCODE='23514';
   END IF;
   INSERT INTO room_bookings(room_id,starts_at,ends_at,title,status,event_id)
    VALUES(NEW.room_id,NEW.starts_at,NEW.ends_at,NEW.title,'confirmed',NEW.id)
    ON CONFLICT(event_id) DO UPDATE SET room_id=EXCLUDED.room_id,starts_at=EXCLUDED.starts_at,
     ends_at=EXCLUDED.ends_at,title=EXCLUDED.title,status='confirmed';
 ELSE
   UPDATE room_bookings SET status='cancelled' WHERE event_id=NEW.id;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS events_occupancy ON events;
CREATE TRIGGER events_occupancy AFTER INSERT OR UPDATE ON events FOR EACH ROW EXECUTE FUNCTION sync_session_occupancy();
-- Backfill if migration is applied to an existing session database.
UPDATE events SET status=status WHERE room_id IS NOT NULL;
