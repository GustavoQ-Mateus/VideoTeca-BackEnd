-- Videoteca Unifor — Migration 015: funções do funcionário (devolução com condição, contas ativas,
-- exemplares extraviados e contador real de inscritos). Idempotente.

-- ── Devolução com condição e observações ─────────────────────────────
ALTER TABLE loans ADD COLUMN IF NOT EXISTS return_condition TEXT;
ALTER TABLE loans ADD COLUMN IF NOT EXISTS return_notes TEXT;
DO $$ BEGIN
  ALTER TABLE loans ADD CONSTRAINT loans_return_condition_chk
    CHECK (return_condition IS NULL OR return_condition IN ('good', 'damaged'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Contas ativas/desativadas ────────────────────────────────────────
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
-- Matrícula/SIAPE única (se a base já tiver duplicatas, o índice não é criado e a API continua checando).
DO $$ BEGIN
  CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_registration_unique
    ON profiles (lower(registration)) WHERE registration IS NOT NULL AND registration <> '';
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'matrículas duplicadas: índice único não criado';
END $$;

-- ── Exemplar extraviado ('lost') ─────────────────────────────────────
ALTER TABLE copies DROP CONSTRAINT IF EXISTS copies_status_chk;
ALTER TABLE copies ADD CONSTRAINT copies_status_chk
  CHECK (status IN ('available', 'reserved', 'borrowed', 'maintenance', 'damaged', 'lost'));

-- No máximo um empréstimo aberto por exemplar (defesa em profundidade do balcão).
DO $$ BEGIN
  CREATE UNIQUE INDEX IF NOT EXISTS idx_loans_one_open_per_copy ON loans (copy_id) WHERE returned_at IS NULL;
EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'exemplar com dois empréstimos abertos: índice não criado';
END $$;
CREATE INDEX IF NOT EXISTS idx_reservations_film_status ON reservations (film_id, status, requested_at);

-- Preferências de notificação: criadas pela migration 014; repetidas aqui só como garantia
-- (app/notify.py consulta esta tabela).
CREATE TABLE IF NOT EXISTS notification_prefs (
  user_id UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  reservations BOOLEAN NOT NULL DEFAULT TRUE,
  events BOOLEAN NOT NULL DEFAULT TRUE,
  reminders BOOLEAN NOT NULL DEFAULT TRUE
);

-- ── events.enrolled = COUNT(event_registrations) ─────────────────────
-- A ocupação de sala só depende de sala, período, filme, título, vagas e status: o trigger da
-- migration 008 deixa de rodar quando só o contador de inscritos muda.
DROP TRIGGER IF EXISTS events_occupancy ON events;
CREATE TRIGGER events_occupancy AFTER INSERT OR UPDATE OF room_id, status, starts_at, ends_at, title, film_id, slots
  ON events FOR EACH ROW EXECUTE FUNCTION sync_session_occupancy();

-- Qualquer escrita em events.enrolled é substituída pela contagem real (o valor informado é ignorado).
CREATE OR REPLACE FUNCTION events_enrolled_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW.enrolled := 0;
  ELSE
    NEW.enrolled := (SELECT COUNT(*) FROM event_registrations WHERE event_id = NEW.id);
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS events_enrolled_guard ON events;
CREATE TRIGGER events_enrolled_guard BEFORE INSERT OR UPDATE OF enrolled ON events
  FOR EACH ROW EXECUTE FUNCTION events_enrolled_guard();

CREATE OR REPLACE FUNCTION sync_event_enrolled() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    UPDATE events SET enrolled = 0 WHERE id = NEW.event_id;  -- o guard grava a contagem
  END IF;
  IF TG_OP IN ('DELETE', 'UPDATE') AND (TG_OP = 'DELETE' OR OLD.event_id IS DISTINCT FROM NEW.event_id) THEN
    UPDATE events SET enrolled = 0 WHERE id = OLD.event_id;
  END IF;
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS event_registrations_enrolled ON event_registrations;
CREATE TRIGGER event_registrations_enrolled AFTER INSERT OR DELETE OR UPDATE OF event_id ON event_registrations
  FOR EACH ROW EXECUTE FUNCTION sync_event_enrolled();

-- Recalcula os contadores existentes (o guard grava COUNT(*)).
UPDATE events e SET enrolled = 0
WHERE e.enrolled IS DISTINCT FROM (SELECT COUNT(*) FROM event_registrations r WHERE r.event_id = e.id);
