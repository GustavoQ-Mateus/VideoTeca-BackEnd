-- Videoteca Unifor — Massa de dados de teste (DEV) — T02.3
-- Idempotente (ON CONFLICT DO NOTHING). NÃO usar em produção.
-- Senha de todos os usuários de teste: videoteca123

-- ── Usuários + perfis (um por papel) ───────────────────────────────
INSERT INTO users (id, email, password_hash) VALUES
  ('11111111-1111-1111-1111-111111111111', 'aluno@edu.unifor.br',       '$2b$12$AeP.TdppFNnYq9nd1HqfHO3bQLyoZv4YXMD1fhbxuEwiUBe3w4WLy'),
  ('22222222-2222-2222-2222-222222222222', 'professor@unifor.br',       '$2b$12$AeP.TdppFNnYq9nd1HqfHO3bQLyoZv4YXMD1fhbxuEwiUBe3w4WLy'),
  ('33333333-3333-3333-3333-333333333333', 'funcionario@unifor.br',     '$2b$12$AeP.TdppFNnYq9nd1HqfHO3bQLyoZv4YXMD1fhbxuEwiUBe3w4WLy'),
  ('44444444-4444-4444-4444-444444444444', 'admin@unifor.br',           '$2b$12$AeP.TdppFNnYq9nd1HqfHO3bQLyoZv4YXMD1fhbxuEwiUBe3w4WLy')
ON CONFLICT (id) DO NOTHING;

INSERT INTO profiles (id, full_name, role, registration, course, phone) VALUES
  ('11111111-1111-1111-1111-111111111111', 'Ana Aluna',        'aluno',       '2020001',   'Cinema e Audiovisual', '85990000001'),
  ('22222222-2222-2222-2222-222222222222', 'Prof. Bruno',      'professor',   'SIAPE1001', 'Comunicação Social',   '85990000002'),
  ('33333333-3333-3333-3333-333333333333', 'Carla Funcionária','funcionario', 'FUNC001',   NULL,                   '85990000003'),
  ('44444444-4444-4444-4444-444444444444', 'Diego Admin',      'admin',       'ADM001',    NULL,                   '85990000004')
ON CONFLICT (id) DO NOTHING;

-- ── Filmes ─────────────────────────────────────────────────────────
-- O acervo vem do TMDB: db/seed/seed_00_catalogo_tmdb.sql roda antes deste arquivo
-- (python -m app.migrate --seed aplica db/seed/seed_*.sql em ordem alfabética).

-- ── Direitos de exibição (T21) — demonstração de autorização vencida (bloqueia publicação) ──
UPDATE films SET rights_valid_until = (NOW() - INTERVAL '1 day')::date WHERE internal_code = 'VT-0002';

-- ── Cópias físicas ─────────────────────────────────────────────────
INSERT INTO copies (id, film_id, code, patrimonio, condition, status) VALUES
  (1, 1, 'CP-0001', 'PAT-1001', 'good',      'available'),
  (2, 1, 'CP-0002', 'PAT-1002', 'good',      'borrowed'),
  (3, 2, 'CP-0003', 'PAT-1003', 'fair',      'available'),
  (4, 3, 'CP-0004', 'PAT-1004', 'excellent', 'available'),
  (5, 5, 'CP-0005', 'PAT-1005', 'damaged',   'maintenance')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('copies', 'id'), (SELECT MAX(id) FROM copies));

-- ── Salas ──────────────────────────────────────────────────────────
INSERT INTO rooms (id, name, capacity, equipment, status) VALUES
  (1, 'Sala de Exibição 1', 40, ARRAY['projetor', 'som surround', 'blu-ray'], 'available'),
  (2, 'Sala de Exibição 2', 25, ARRAY['projetor', 'som estéreo'],             'available'),
  (3, 'Mini Auditório',     80, ARRAY['projetor', 'som surround', 'palco'],   'available')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('rooms', 'id'), (SELECT MAX(id) FROM rooms));

-- ── Reservas de sala (sem sobreposição — respeitam o anticonflito) ─
INSERT INTO room_bookings (id, room_id, professor_id, starts_at, ends_at, title, status) VALUES
  (1, 1, '22222222-2222-2222-2222-222222222222', NOW() + INTERVAL '1 day',  NOW() + INTERVAL '1 day 2 hours',  'Mostra de Cinema Brasileiro', 'confirmed'),
  (2, 1, '22222222-2222-2222-2222-222222222222', NOW() + INTERVAL '1 day 3 hours', NOW() + INTERVAL '1 day 5 hours', 'Debate: Documentário', 'confirmed'),
  (3, 2, '22222222-2222-2222-2222-222222222222', NOW() + INTERVAL '2 days', NOW() + INTERVAL '2 days 2 hours', 'Aula de Análise Fílmica', 'pending')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('room_bookings', 'id'), (SELECT MAX(id) FROM room_bookings));

-- ── Reservas de filme e empréstimo ─────────────────────────────────
INSERT INTO reservations (id, user_id, film_id, status, pickup_until) VALUES
  (1, '11111111-1111-1111-1111-111111111111', 3, 'ready', NOW() + INTERVAL '3 days')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('reservations', 'id'), (SELECT MAX(id) FROM reservations));

INSERT INTO loans (id, user_id, copy_id, borrowed_at, due_at, status) VALUES
  (1, '11111111-1111-1111-1111-111111111111', 2, NOW() - INTERVAL '2 days', NOW() + INTERVAL '5 days', 'borrowed')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('loans', 'id'), (SELECT MAX(id) FROM loans));

-- ── Eventos ────────────────────────────────────────────────────────
INSERT INTO events (id, title, event_type, film_id, location, starts_at, slots, enrolled, description, certificate, status) VALUES
  (1, 'Cineclube: Cidade de Deus', 'exibicao', 1, 'Sala de Exibição 1', NOW() + INTERVAL '5 days', 40, 12, 'Exibição seguida de debate.', TRUE,  'published'),
  (2, 'Oficina de Roteiro',        'oficina',  NULL, 'Mini Auditório',   NOW() + INTERVAL '7 days', 30, 0,  'Introdução à escrita de roteiro audiovisual.', TRUE, 'published')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('events', 'id'), (SELECT MAX(id) FROM events));

INSERT INTO event_registrations (event_id, user_id, present) VALUES
  (1, '11111111-1111-1111-1111-111111111111', FALSE)
ON CONFLICT (event_id, user_id) DO NOTHING;

-- ── Notificações ───────────────────────────────────────────────────
INSERT INTO notifications (user_id, title, body, read) VALUES
  ('11111111-1111-1111-1111-111111111111', 'Reserva pronta para retirada', 'Sua reserva de "Parasita" está disponível na videoteca.', FALSE)
ON CONFLICT DO NOTHING;
