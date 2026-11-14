-- Videoteca Unifor — Migration 017: turmas (aula só para os alunos da turma) e
-- contato/aviso exibidos em "Fale com a Videoteca". Idempotente.

-- ── Turmas ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS turmas (
    id            BIGSERIAL PRIMARY KEY,
    code          TEXT NOT NULL CHECK (char_length(code) BETWEEN 1 AND 20),
    discipline    TEXT NOT NULL CHECK (char_length(discipline) BETWEEN 2 AND 160),
    semester      TEXT NOT NULL DEFAULT '2026.2' CHECK (char_length(semester) BETWEEN 4 AND 10),
    professor_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (discipline, code, semester)
);
CREATE INDEX IF NOT EXISTS idx_turmas_professor ON turmas (professor_id);

CREATE TABLE IF NOT EXISTS turma_alunos (
    turma_id  BIGINT NOT NULL REFERENCES turmas(id) ON DELETE CASCADE,
    user_id   UUID   NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    PRIMARY KEY (turma_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_turma_alunos_user ON turma_alunos (user_id);

-- Turmas atendidas por um pedido do professor (sala ou filme para aula).
ALTER TABLE professor_requests ADD COLUMN IF NOT EXISTS turma_ids BIGINT[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_prof_requests_turmas ON professor_requests USING GIN (turma_ids);

-- ── Contato e aviso (linha única de app_settings) ───────────────────
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS contact_location TEXT NOT NULL DEFAULT '1º andar do Centro de Convivência · setor da Biblioteca Central';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS contact_hours    TEXT NOT NULL DEFAULT 'Seg. a sex., 7h às 22h40 · sábado, 8h às 11h20';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS contact_phones   TEXT NOT NULL DEFAULT '(85) 3477-3163 · (85) 3477-3308';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS contact_email    TEXT NOT NULL DEFAULT 'videoteca@unifor.br';
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS notice           TEXT;
