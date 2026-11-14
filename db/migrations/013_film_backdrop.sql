-- Redesign: cena (backdrop) do filme para áreas largas da interface. Idempotente.
ALTER TABLE films ADD COLUMN IF NOT EXISTS backdrop TEXT;
