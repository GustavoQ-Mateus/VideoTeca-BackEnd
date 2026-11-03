-- T22.3 Índices compostos para as consultas quentes (meta: toda requisição < 60 s). Idempotente.
-- Catálogo: WHERE status<>'inactive' ORDER BY title (paginação por LIMIT/OFFSET).
CREATE INDEX IF NOT EXISTS idx_films_title_active ON films (title) WHERE status <> 'inactive';
-- Eventos públicos e painel: WHERE status='published' ORDER BY starts_at.
CREATE INDEX IF NOT EXISTS idx_events_status_starts ON events (status, starts_at);
CREATE INDEX IF NOT EXISTS idx_events_room_starts ON events (room_id, starts_at) WHERE room_id IS NOT NULL;
-- Empréstimos/reservas do usuário filtrados por status.
CREATE INDEX IF NOT EXISTS idx_loans_user_status ON loans (user_id, status);
CREATE INDEX IF NOT EXISTS idx_reservations_user_status ON reservations (user_id, status);
CREATE INDEX IF NOT EXISTS idx_equipment_loans_user_open ON equipment_loans (user_id) WHERE returned_at IS NULL;
-- Reservas de sala por professor (reservas/me) e disponibilidade por sala/período.
CREATE INDEX IF NOT EXISTS idx_room_bookings_prof_starts ON room_bookings (professor_id, starts_at DESC);
CREATE INDEX IF NOT EXISTS idx_room_bookings_room_period ON room_bookings (room_id, starts_at, ends_at) WHERE status <> 'cancelled';
CREATE INDEX IF NOT EXISTS idx_prof_requests_prof_created ON professor_requests (professor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications (user_id, created_at DESC);
