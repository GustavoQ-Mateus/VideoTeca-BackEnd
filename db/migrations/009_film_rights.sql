-- T21.1 Autorização de exibição (direitos autorais) no cadastro do filme. Idempotente.
-- Filmes existentes ficam 'pending': precisam ser revisados antes de nova publicação.
ALTER TABLE films ADD COLUMN IF NOT EXISTS rights_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE films ADD COLUMN IF NOT EXISTS rights_holder TEXT NOT NULL DEFAULT '';
ALTER TABLE films ADD COLUMN IF NOT EXISTS license_type TEXT NOT NULL DEFAULT '';
ALTER TABLE films ADD COLUMN IF NOT EXISTS rights_valid_until DATE;
ALTER TABLE films ADD COLUMN IF NOT EXISTS rights_notes TEXT NOT NULL DEFAULT '';
DO $$ BEGIN
 ALTER TABLE films ADD CONSTRAINT films_rights_status_chk
  CHECK (rights_status IN ('pending','authorized','denied','public_domain'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
 ALTER TABLE films ADD CONSTRAINT films_rights_holder_chk
  CHECK (rights_status <> 'authorized' OR btrim(rights_holder) <> '');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_films_rights_status ON films (rights_status);
