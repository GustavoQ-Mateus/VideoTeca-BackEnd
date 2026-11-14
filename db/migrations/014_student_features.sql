-- Migration 014 — funcionalidades do aluno: favoritos, preferências de notificação e renovação.
-- Idempotente.
CREATE TABLE IF NOT EXISTS favorites (
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    film_id BIGINT NOT NULL REFERENCES films(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, film_id)
);
CREATE INDEX IF NOT EXISTS idx_favorites_film_id ON favorites (film_id);

CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    reservations BOOLEAN NOT NULL DEFAULT TRUE,
    events BOOLEAN NOT NULL DEFAULT TRUE,
    reminders BOOLEAN NOT NULL DEFAULT TRUE
);

ALTER TABLE loans ADD COLUMN IF NOT EXISTS renewals INT NOT NULL DEFAULT 0;

-- Índices de empréstimo aberto por cópia e reservas por filme/status ficam na migration 015.
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications (user_id) WHERE read = FALSE;
