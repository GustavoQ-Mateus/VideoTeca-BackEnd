-- T21.3 Comprovantes de autorização de exibição. Idempotente.
CREATE TABLE IF NOT EXISTS film_rights_proofs (
 id BIGSERIAL PRIMARY KEY,
 film_id BIGINT NOT NULL REFERENCES films(id) ON DELETE CASCADE,
 filename TEXT NOT NULL CHECK (length(filename) BETWEEN 1 AND 120),
 media_type TEXT NOT NULL CHECK (media_type IN ('application/pdf','image/png','image/jpeg')),
 content BYTEA NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 10485760),
 sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 uploaded_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
 uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_film_rights_proofs_film ON film_rights_proofs (film_id, uploaded_at DESC);
-- Defesa em profundidade da regra de T21.2: nenhum caminho (sessões, eventos legados) publica
-- filme sem autorização válida. Só valida quando a publicação começa ou muda filme/período.
CREATE OR REPLACE FUNCTION check_event_film_rights() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE f films%ROWTYPE;
BEGIN
 IF NEW.status <> 'published' OR NEW.film_id IS NULL THEN RETURN NEW; END IF;
 IF TG_OP = 'UPDATE' AND OLD.status = 'published' AND OLD.film_id = NEW.film_id
    AND OLD.ends_at IS NOT DISTINCT FROM NEW.ends_at AND OLD.starts_at IS NOT DISTINCT FROM NEW.starts_at THEN
   RETURN NEW;
 END IF;
 SELECT * INTO f FROM films WHERE id = NEW.film_id;
 IF NOT FOUND OR f.rights_status NOT IN ('authorized','public_domain')
    OR (f.rights_valid_until IS NOT NULL AND f.rights_valid_until <
        (COALESCE(NEW.ends_at, NEW.starts_at, NOW()) AT TIME ZONE 'America/Fortaleza')::date)
    OR (f.rights_status = 'authorized' AND NOT EXISTS (SELECT 1 FROM film_rights_proofs p WHERE p.film_id = f.id)) THEN
   RAISE EXCEPTION 'Filme sem autorizacao de exibicao' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS events_film_rights ON events;
CREATE TRIGGER events_film_rights BEFORE INSERT OR UPDATE ON events
 FOR EACH ROW EXECUTE FUNCTION check_event_film_rights();
