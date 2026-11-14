-- Videoteca Unifor — Massa de demonstração completa (DEV). NÃO usar em produção.
-- Roda depois de seed_dev.sql e seed_zz_programacao_tmdb.sql. Idempotente: se a conta
-- beatriz.lima@edu.unifor.br já existe, nada é feito. Datas relativas a NOW().
-- Senha de todas as contas: videoteca123 (mesmo hash de seed_dev.sql).

DO $$
DECLARE
  pw CONSTANT text := '$2b$12$AeP.TdppFNnYq9nd1HqfHO3bQLyoZv4YXMD1fhbxuEwiUBe3w4WLy';
  staff1 uuid := '33333333-3333-3333-3333-333333333333';
  staff2 uuid;
  prof_ids uuid[];
  aluno_ids uuid[];
  everyone uuid[];
  u uuid; p uuid;
  f record; c record; e record;
  i int; j int; k int; n int;
  fid bigint; cid bigint; tid bigint; eid bigint; bid bigint; rid bigint;
  t_start timestamptz; t_end timestamptz; d date;
  films_ok bigint[];
  series_ids bigint[] := '{}';
  turma_ids bigint[];
  slots CONSTANT text[][] := ARRAY[['07:30','09:10'],['09:20','11:00'],['13:30','15:10'],['15:30','17:10'],['18:10','19:50']];
BEGIN
  IF EXISTS (SELECT 1 FROM users WHERE email = 'beatriz.lima@edu.unifor.br') THEN
    RAISE NOTICE 'seed_zzz_demo_completo: já aplicado, nada a fazer';
    RETURN;
  END IF;
  PERFORM setseed(0.42);

  -- ── Contas ─────────────────────────────────────────────────────────
  CREATE TEMP TABLE demo_people(email text, name text, role text, reg text, course text, phone text) ON COMMIT DROP;
  INSERT INTO demo_people VALUES
    ('beatriz.lima@edu.unifor.br',     'Beatriz Lima',       'aluno', '2021002', 'Cinema e Audiovisual', '85991000002'),
    ('caio.nogueira@edu.unifor.br',    'Caio Nogueira',      'aluno', '2021003', 'Jornalismo',           '85991000003'),
    ('daniela.rocha@edu.unifor.br',    'Daniela Rocha',      'aluno', '2021004', 'Publicidade e Propaganda', '85991000004'),
    ('eduardo.pinheiro@edu.unifor.br', 'Eduardo Pinheiro',   'aluno', '2022005', 'Cinema e Audiovisual', '85991000005'),
    ('fernanda.alves@edu.unifor.br',   'Fernanda Alves',     'aluno', '2022006', 'Letras',               '85991000006'),
    ('gabriel.sousa@edu.unifor.br',    'Gabriel Sousa',      'aluno', '2022007', 'Direito',              '85991000007'),
    ('helena.martins@edu.unifor.br',   'Helena Martins',     'aluno', '2023008', 'Psicologia',           '85991000008'),
    ('igor.bezerra@edu.unifor.br',     'Igor Bezerra',       'aluno', '2023009', 'Cinema e Audiovisual', '85991000009'),
    ('julia.freitas@edu.unifor.br',    'Júlia Freitas',      'aluno', '2023010', 'Jornalismo',           '85991000010'),
    ('lucas.holanda@edu.unifor.br',    'Lucas Holanda',      'aluno', '2024011', 'Publicidade e Propaganda', '85991000011'),
    ('marina.castro@unifor.br',        'Profa. Marina Castro',  'professor', 'SIAPE1002', 'Cinema e Audiovisual', '85992000002'),
    ('rafael.teixeira@unifor.br',      'Prof. Rafael Teixeira', 'professor', 'SIAPE1003', 'Letras',               '85992000003'),
    ('silvia.arruda@unifor.br',        'Profa. Sílvia Arruda',  'professor', 'SIAPE1004', 'História',             '85992000004'),
    ('renata.oliveira@unifor.br',      'Renata Oliveira',    'funcionario', 'FUNC002', NULL, '85993000002');

  FOR e IN SELECT * FROM demo_people LOOP
    INSERT INTO users(id, email, password_hash) VALUES (gen_random_uuid(), e.email, pw) RETURNING id INTO u;
    INSERT INTO profiles(id, full_name, role, registration, course, phone) VALUES (u, e.name, e.role, e.reg, e.course, e.phone);
  END LOOP;

  SELECT id INTO staff2 FROM users WHERE email = 'renata.oliveira@unifor.br';
  SELECT array_agg(id ORDER BY registration) INTO prof_ids FROM profiles WHERE role = 'professor';
  SELECT array_agg(id ORDER BY registration) INTO aluno_ids FROM profiles WHERE role = 'aluno';
  SELECT array_agg(id) INTO everyone FROM profiles WHERE role IN ('aluno', 'professor');

  -- Preferências e aceite da política para todo mundo
  INSERT INTO notification_prefs(user_id, reservations, events, reminders)
    SELECT id, TRUE, TRUE, (random() > 0.2) FROM profiles ON CONFLICT DO NOTHING;
  INSERT INTO privacy_consents(user_id, policy_version, accepted_at)
    SELECT id, '2026-10', NOW() - make_interval(days => (random()*40)::int) FROM profiles ON CONFLICT DO NOTHING;

  -- ── Turmas ─────────────────────────────────────────────────────────
  INSERT INTO turmas(code, discipline, semester, professor_id) VALUES
    ('T01', 'Linguagem Cinematográfica', '2026.2', prof_ids[2]),
    ('T02', 'Linguagem Cinematográfica', '2026.2', prof_ids[2]),
    ('T01', 'Documentário Brasileiro',   '2026.2', prof_ids[2]),
    ('T01', 'Literatura e Cinema',       '2026.2', prof_ids[3]),
    ('T03', 'Literatura e Cinema',       '2026.2', prof_ids[3]),
    ('T01', 'História do Brasil Contemporâneo', '2026.2', prof_ids[4])
  ON CONFLICT DO NOTHING;
  SELECT array_agg(id ORDER BY id) INTO turma_ids FROM turmas WHERE semester = '2026.2';
  -- Cada aluno em 2 a 4 turmas
  FOREACH u IN ARRAY aluno_ids LOOP
    INSERT INTO turma_alunos(turma_id, user_id)
      SELECT t, u FROM unnest(turma_ids) t WHERE random() < 0.45 ON CONFLICT DO NOTHING;
    INSERT INTO turma_alunos(turma_id, user_id) VALUES (turma_ids[1 + floor(random()*array_length(turma_ids,1))::int], u) ON CONFLICT DO NOTHING;
  END LOOP;

  -- ── Acervo: séries do Vai de Série, regras por filme, direitos ─────
  FOR e IN SELECT * FROM (VALUES
      ('Cidade dos Homens (série)', 2002, 'Fernando Meirelles, Paulo Morelli', 'Série · Drama', 'VS-0001', 'Episódios da série sobre Acerola e Laranjinha no Rio de Janeiro.'),
      ('Sintonia (série)',          2019, 'KondZilla, Felipe Braga',            'Série · Drama', 'VS-0002', 'Três amigos de uma comunidade de São Paulo entre o funk, o tráfico e a igreja.'),
      ('Irmandade (série)',         2019, 'Pedro Morelli',                      'Série · Crime', 'VS-0003', 'Uma advogada é forçada a se infiltrar na facção liderada pelo irmão.'),
      ('Os Normais (série)',        2001, 'José Alvarenga Jr.',                 'Série · Comédia', 'VS-0004', 'O cotidiano de um casal de noivos às voltas com as próprias manias.')
    ) AS s(title, year, director, genre, code, synopsis)
  LOOP
    INSERT INTO films(title, year, director, genre, media, status, location, synopsis, country, language, internal_code,
                      rights_status, rights_holder, license_type, rights_valid_until, duration, rating)
    VALUES (e.title, e.year, e.director, e.genre, 'DVD', 'available', 'Estante S1', e.synopsis, 'Brasil', 'Português', e.code,
            'authorized', 'Distribuidora parceira (licença educacional)', 'Exibição pública não comercial', (NOW() + INTERVAL '1 year')::date, '4 episódios', '14')
    RETURNING id INTO fid;
    series_ids := series_ids || fid;
    INSERT INTO copies(film_id, code, patrimonio, condition, status) VALUES
      (fid, e.code || '-A', 'PAT-' || e.code || '-A', 'Bom', 'available'),
      (fid, e.code || '-B', 'PAT-' || e.code || '-B', 'Bom', 'available');
    INSERT INTO film_rights_proofs(film_id, filename, media_type, content, sha256, uploaded_by)
      VALUES (fid, 'licenca-' || lower(e.code) || '.pdf', 'application/pdf', convert_to('%PDF-1.4 licença de demonstração ' || e.code, 'UTF8'),
              encode(sha256(convert_to('%PDF-1.4 licença de demonstração ' || e.code, 'UTF8')), 'hex'), staff1);
  END LOOP;

  -- Regras próprias em alguns filmes
  UPDATE films SET loan_days = 3, max_renewals = 0, queue_limit = 5 WHERE id IN (SELECT id FROM films WHERE year >= 2022 AND internal_code NOT LIKE 'VS-%' ORDER BY id LIMIT 3);
  UPDATE films SET loan_days = 14, max_renewals = 2 WHERE id IN (SELECT id FROM films WHERE genre ILIKE '%document%' ORDER BY id LIMIT 3);
  UPDATE films SET local_only = TRUE WHERE id = (SELECT id FROM films WHERE year < 1960 AND internal_code NOT LIKE 'VS-%' ORDER BY year LIMIT 1);
  UPDATE films SET pickup_days = 2 WHERE id IN (SELECT id FROM films WHERE internal_code NOT LIKE 'VS-%' ORDER BY id DESC LIMIT 4);

  -- Cópias com problema (manutenção/danificada) para o acervo e relatórios
  UPDATE copies SET status = 'maintenance', condition = 'Risco leve no disco' WHERE id = (SELECT id FROM copies WHERE status = 'available' ORDER BY id DESC LIMIT 1 OFFSET 3);
  UPDATE copies SET status = 'damaged', condition = 'Disco trincado' WHERE id = (SELECT id FROM copies WHERE status = 'available' ORDER BY id DESC LIMIT 1 OFFSET 9);

  SELECT array_agg(id) INTO films_ok FROM films
   WHERE status = 'available' AND NOT local_only
     AND (rights_status = 'public_domain' OR (rights_status = 'authorized' AND EXISTS (SELECT 1 FROM film_rights_proofs pr WHERE pr.film_id = films.id)))
     AND (rights_valid_until IS NULL OR rights_valid_until > (NOW() + INTERVAL '30 days')::date);

  -- ── Empréstimos: histórico devolvido (6 meses), em aberto e atrasados ─
  -- Histórico: ~70 empréstimos devolvidos, sem sobrepor a mesma cópia.
  FOR i IN 1..70 LOOP
    u := everyone[1 + floor(random()*array_length(everyone,1))::int];
    SELECT id INTO cid FROM copies WHERE film_id IN (SELECT id FROM films WHERE internal_code NOT LIKE 'VS-%' OR random() < 0.3) ORDER BY random() LIMIT 1;
    t_start := NOW() - make_interval(days => 10 + floor(random()*170)::int, hours => floor(random()*8)::int);
    IF EXISTS (SELECT 1 FROM loans WHERE copy_id = cid AND tstzrange(borrowed_at, COALESCE(returned_at, NOW())) && tstzrange(t_start, t_start + INTERVAL '9 days')) THEN CONTINUE; END IF;
    INSERT INTO loans(user_id, copy_id, borrowed_at, due_at, returned_at, status, renewals, return_condition, return_notes)
    VALUES (u, cid, t_start, t_start + INTERVAL '7 days',
            t_start + make_interval(days => 2 + floor(random()*8)::int),
            'returned', (random() < 0.2)::int,
            CASE WHEN random() < 0.05 THEN 'damaged' ELSE 'good' END,
            CASE WHEN random() < 0.05 THEN 'Caixa trincada na devolução' END);
  END LOOP;

  -- Em aberto: 1 ou 2 por aluno, alguns por professor; ~4 em atraso
  n := 0;
  FOREACH u IN ARRAY aluno_ids || prof_ids[2:4] LOOP
    FOR j IN 1..(1 + (random() < 0.5)::int) LOOP
      SELECT c2.id INTO cid FROM copies c2 JOIN films f2 ON f2.id = c2.film_id
       WHERE c2.status = 'available' AND NOT f2.local_only AND f2.status = 'available'
         AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = c2.id AND l.returned_at IS NULL)
       ORDER BY random() LIMIT 1;
      EXIT WHEN cid IS NULL;
      n := n + 1;
      IF n % 5 = 0 THEN
        t_start := NOW() - make_interval(days => 10 + floor(random()*6)::int);   -- atrasado
      ELSE
        t_start := NOW() - make_interval(days => floor(random()*5)::int, hours => floor(random()*6)::int);
      END IF;
      INSERT INTO loans(user_id, copy_id, borrowed_at, due_at, status, renewals)
      VALUES (u, cid, t_start, t_start + INTERVAL '7 days', 'borrowed', (n % 4 = 0)::int);
      UPDATE copies SET status = 'borrowed' WHERE id = cid;
    END LOOP;
  END LOOP;

  -- ── Reservas: fila, prontas, histórico ─────────────────────────────
  -- Fila (waiting) em filmes sem cópia livre
  FOR f IN SELECT fl.id FROM films fl WHERE NOT fl.local_only AND NOT EXISTS (
             SELECT 1 FROM copies c3 WHERE c3.film_id = fl.id AND c3.status = 'available') ORDER BY random() LIMIT 5 LOOP
    FOR j IN 1..(1 + floor(random()*3)::int) LOOP
      u := aluno_ids[1 + floor(random()*array_length(aluno_ids,1))::int];
      CONTINUE WHEN EXISTS (SELECT 1 FROM reservations r WHERE r.user_id = u AND r.film_id = f.id AND r.status IN ('waiting','ready'));
      INSERT INTO reservations(user_id, film_id, status, requested_at) VALUES (u, f.id, 'waiting', NOW() - make_interval(hours => j*7 + floor(random()*20)::int));
    END LOOP;
  END LOOP;
  -- Prontas para retirada (copia separada)
  FOR i IN 1..4 LOOP
    SELECT c4.id, c4.film_id INTO c FROM copies c4 JOIN films f4 ON f4.id = c4.film_id
      WHERE c4.status = 'available' AND NOT f4.local_only AND f4.internal_code NOT LIKE 'VS-%' ORDER BY random() LIMIT 1;
    u := aluno_ids[1 + ((i * 3) % array_length(aluno_ids,1))];
    INSERT INTO reservations(user_id, film_id, status, requested_at, pickup_until)
      VALUES (u, c.film_id, 'ready', NOW() - INTERVAL '2 days', NOW() + make_interval(days => 1 + i % 3));
    UPDATE copies SET status = 'reserved' WHERE id = c.id;
  END LOOP;
  -- Histórico: atendidas, canceladas, expiradas
  FOR i IN 1..25 LOOP
    u := aluno_ids[1 + floor(random()*array_length(aluno_ids,1))::int];
    fid := films_ok[1 + floor(random()*array_length(films_ok,1))::int];
    INSERT INTO reservations(user_id, film_id, status, requested_at, pickup_until)
    VALUES (u, fid, (ARRAY['fulfilled','fulfilled','fulfilled','cancelled','expired'])[1 + floor(random()*5)::int],
            NOW() - make_interval(days => 5 + floor(random()*150)::int), NULL);
  END LOOP;

  -- ── Favoritos ──────────────────────────────────────────────────────
  FOREACH u IN ARRAY aluno_ids LOOP
    INSERT INTO favorites(user_id, film_id, created_at)
      SELECT u, id, NOW() - make_interval(days => floor(random()*60)::int) FROM films WHERE random() < 0.15 ON CONFLICT DO NOTHING;
  END LOOP;

  -- ── Sessões: histórico (encerradas) e Vai de Série futuras ─────────
  -- BibCine às sextas 19h na Sala C nas últimas 10 semanas; Vai de Série às quartas 16h na Sala B.
  FOR i IN 1..10 LOOP
    d := (date_trunc('week', NOW() AT TIME ZONE 'America/Fortaleza')::date - 7*i) + 4;   -- sexta
    fid := films_ok[1 + ((i * 7) % array_length(films_ok,1))];
    SELECT title INTO f FROM films WHERE id = fid;
    INSERT INTO events(title, event_type, film_id, location, starts_at, ends_at, slots, description, certificate, status, room_id)
    VALUES ('BibCine: ' || f.title, 'exibicao', fid, 'Sala C', (d + TIME '19:00') AT TIME ZONE 'America/Fortaleza', (d + TIME '21:30') AT TIME ZONE 'America/Fortaleza',
            80, 'Exibição seguida de debate com a equipe da Videoteca.', TRUE, 'finished', 3)
    RETURNING id INTO eid;
    INSERT INTO event_registrations(event_id, user_id, present)
      SELECT eid, x, random() < 0.8 FROM unnest(everyone) x WHERE random() < 0.55 ON CONFLICT DO NOTHING;

    d := d - 2;   -- quarta
    fid := series_ids[1 + (i % array_length(series_ids,1))];
    SELECT title INTO f FROM films WHERE id = fid;
    INSERT INTO events(title, event_type, film_id, location, starts_at, ends_at, slots, description, certificate, status, room_id)
    VALUES ('Vai de Série: ' || replace(f.title, ' (série)', ''), 'exibicao', fid, 'Sala B', (d + TIME '16:00') AT TIME ZONE 'America/Fortaleza', (d + TIME '18:00') AT TIME ZONE 'America/Fortaleza',
            25, 'Dois episódios seguidos de conversa sobre a série.', FALSE, 'finished', 2)
    RETURNING id INTO eid;
    INSERT INTO event_registrations(event_id, user_id, present)
      SELECT eid, x, random() < 0.75 FROM unnest(aluno_ids) x WHERE random() < 0.5 ON CONFLICT DO NOTHING;
  END LOOP;

  -- Vai de Série nas próximas 4 quartas (publicadas; ocupam a Sala B)
  FOR i IN 0..3 LOOP
    d := (date_trunc('week', NOW() AT TIME ZONE 'America/Fortaleza')::date + 7*i) + 2;
    CONTINUE WHEN d < (NOW() AT TIME ZONE 'America/Fortaleza')::date;
    t_start := (d + TIME '16:00') AT TIME ZONE 'America/Fortaleza';
    t_end := (d + TIME '18:00') AT TIME ZONE 'America/Fortaleza';
    CONTINUE WHEN EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = 2 AND b.status <> 'cancelled' AND tstzrange(b.starts_at, b.ends_at) && tstzrange(t_start, t_end));
    fid := series_ids[1 + (i % array_length(series_ids,1))];
    SELECT title INTO f FROM films WHERE id = fid;
    INSERT INTO events(title, event_type, film_id, location, starts_at, ends_at, slots, description, certificate, status, room_id)
    VALUES ('Vai de Série: ' || replace(f.title, ' (série)', ''), 'exibicao', fid, 'Sala B', t_start, t_end,
            25, 'Dois episódios seguidos de conversa sobre a série.', FALSE, 'published', 2)
    RETURNING id INTO eid;
    INSERT INTO event_registrations(event_id, user_id, present)
      SELECT eid, x, FALSE FROM unnest(aluno_ids) x WHERE random() < 0.35 ON CONFLICT DO NOTHING;
  END LOOP;
  -- Um rascunho para a equipe revisar
  INSERT INTO events(title, event_type, film_id, location, starts_at, ends_at, slots, description, certificate, status, room_id)
  SELECT 'Vai de Série: Os Normais', 'exibicao', series_ids[4], 'Sala B',
         ((date_trunc('week', NOW() AT TIME ZONE 'America/Fortaleza')::date + 35) + TIME '16:00') AT TIME ZONE 'America/Fortaleza',
         ((date_trunc('week', NOW() AT TIME ZONE 'America/Fortaleza')::date + 35) + TIME '18:00') AT TIME ZONE 'America/Fortaleza',
         25, 'Rascunho: confirmar episódios com a distribuidora.', FALSE, 'draft', 2;
  -- Inscrições nas sessões BibCine já publicadas
  INSERT INTO event_registrations(event_id, user_id, present)
    SELECT ev.id, x, FALSE FROM events ev CROSS JOIN unnest(everyone) x
     WHERE ev.status = 'published' AND ev.starts_at > NOW() AND ev.title LIKE 'BibCine:%' AND random() < 0.3
    ON CONFLICT DO NOTHING;

  -- ── Pedidos de sala e filme para aula ──────────────────────────────
  -- Professores: aulas nas Salas A e B nas próximas 3 semanas e nas últimas 6 (histórico)
  FOR i IN 1..40 LOOP
    p := prof_ids[1 + (i % array_length(prof_ids,1))];
    d := (NOW() AT TIME ZONE 'America/Fortaleza')::date + (-42 + floor(random()*63)::int);
    CONTINUE WHEN extract(isodow FROM d) > 5;
    j := 1 + floor(random()*5)::int;
    rid := 1 + (random() < 0.5)::int;
    t_start := (d + slots[j][1]::time) AT TIME ZONE 'America/Fortaleza';
    t_end := (d + slots[j][2]::time) AT TIME ZONE 'America/Fortaleza';
    CONTINUE WHEN EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = rid AND b.status <> 'cancelled' AND tstzrange(b.starts_at, b.ends_at) && tstzrange(t_start, t_end));
    SELECT array_agg(id) INTO turma_ids FROM (SELECT id FROM turmas WHERE professor_id = p ORDER BY random() LIMIT 1 + (random() < 0.3)::int) s;
    k := CASE WHEN t_start < NOW() THEN 1 ELSE floor(random()*10)::int END;   -- passado: aprovado
    INSERT INTO room_bookings(room_id, professor_id, starts_at, ends_at, title, status, course, registration, student_count, notes)
    SELECT rid, p, t_start, t_end,
           (ARRAY['Exibição e debate', 'Análise de cenas', 'Aula com trechos de filmes', 'Seminário da turma', 'Mostra de curtas da turma'])[1 + floor(random()*5)::int],
           CASE WHEN k <= 6 THEN 'confirmed' WHEN k <= 8 THEN 'pending' ELSE 'cancelled' END,
           COALESCE((SELECT string_agg(discipline || ' (' || code || ')', ', ') FROM turmas WHERE id = ANY(turma_ids)), 'Aula'),
           pr.registration, 15 + floor(random()*20)::int, NULL
      FROM profiles pr WHERE pr.id = p
    RETURNING id INTO bid;
    INSERT INTO professor_requests(professor_id, request_type, payload, status, staff_notes, created_at, booking_id, turma_ids)
    SELECT p, 'room',
           jsonb_build_object('room_id', b.room_id, 'room_name', r.name, 'title', b.title, 'course', b.course, 'registration', b.registration,
                              'student_count', b.student_count, 'starts_at', to_char(b.starts_at AT TIME ZONE 'America/Fortaleza', 'YYYY-MM-DD"T"HH24:MI:SS') || '-03:00',
                              'ends_at', to_char(b.ends_at AT TIME ZONE 'America/Fortaleza', 'YYYY-MM-DD"T"HH24:MI:SS') || '-03:00', 'notes', NULL,
                              'turma_ids', to_jsonb(COALESCE(turma_ids, '{}')),
                              'turmas', (SELECT COALESCE(jsonb_agg(discipline || ' (' || code || ')'), '[]'::jsonb) FROM turmas WHERE id = ANY(turma_ids))),
           CASE b.status WHEN 'confirmed' THEN 'approved' WHEN 'pending' THEN 'analysis' ELSE 'refused' END,
           CASE b.status WHEN 'confirmed' THEN 'Sala reservada. O projetor fica ligado 10 minutos antes.' WHEN 'cancelled' THEN 'Sala ocupada por manutenção neste horário.' END,
           LEAST(b.starts_at - INTERVAL '5 days', NOW() - INTERVAL '1 hour'), b.id, COALESCE(turma_ids, '{}')
      FROM room_bookings b JOIN rooms r ON r.id = b.room_id WHERE b.id = bid;
  END LOOP;

  -- Alunos pedindo sala para estudo em grupo
  FOR i IN 1..8 LOOP
    u := aluno_ids[1 + (i % array_length(aluno_ids,1))];
    d := (NOW() AT TIME ZONE 'America/Fortaleza')::date + (1 + floor(random()*14)::int);
    CONTINUE WHEN extract(isodow FROM d) > 5;
    j := 1 + floor(random()*5)::int;
    t_start := (d + slots[j][1]::time) AT TIME ZONE 'America/Fortaleza';
    t_end := (d + slots[j][2]::time) AT TIME ZONE 'America/Fortaleza';
    CONTINUE WHEN EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = 2 AND b.status <> 'cancelled' AND tstzrange(b.starts_at, b.ends_at) && tstzrange(t_start, t_end));
    INSERT INTO room_bookings(room_id, professor_id, starts_at, ends_at, title, status, course, registration, student_count)
    SELECT 2, u, t_start, t_end, 'Grupo de estudo: ' || (ARRAY['roteiro do TCC', 'montagem do curta', 'seminário de cinema', 'análise de documentário'])[1 + (i % 4)],
           CASE WHEN i % 3 = 0 THEN 'confirmed' ELSE 'pending' END, pr.course, pr.registration, 3 + i % 5
      FROM profiles pr WHERE pr.id = u
    RETURNING id INTO bid;
    INSERT INTO professor_requests(professor_id, request_type, payload, status, staff_notes, created_at, booking_id, turma_ids)
    SELECT u, 'room',
           jsonb_build_object('room_id', 2, 'room_name', 'Sala B', 'title', b.title, 'course', b.course, 'registration', b.registration,
                              'student_count', b.student_count, 'starts_at', to_char(b.starts_at AT TIME ZONE 'America/Fortaleza', 'YYYY-MM-DD"T"HH24:MI:SS') || '-03:00',
                              'ends_at', to_char(b.ends_at AT TIME ZONE 'America/Fortaleza', 'YYYY-MM-DD"T"HH24:MI:SS') || '-03:00', 'notes', NULL, 'turma_ids', '[]'::jsonb, 'turmas', '[]'::jsonb),
           CASE b.status WHEN 'confirmed' THEN 'approved' ELSE 'analysis' END,
           CASE b.status WHEN 'confirmed' THEN 'Sala B reservada para o grupo.' END,
           NOW() - make_interval(hours => 2 + i * 5), b.id, '{}'
      FROM room_bookings b WHERE b.id = bid;
  END LOOP;

  -- Filmes para aula
  FOR i IN 1..14 LOOP
    p := prof_ids[1 + (i % array_length(prof_ids,1))];
    fid := films_ok[1 + floor(random()*array_length(films_ok,1))::int];
    SELECT array_agg(id) INTO turma_ids FROM (SELECT id FROM turmas WHERE professor_id = p ORDER BY random() LIMIT 1) s;
    d := (NOW() AT TIME ZONE 'America/Fortaleza')::date + (-30 + floor(random()*50)::int);
    INSERT INTO professor_requests(professor_id, request_type, payload, status, staff_notes, created_at, turma_ids)
    SELECT p, 'film',
           jsonb_build_object('film_id', fl.id, 'film_title', fl.title, 'date', to_char(d, 'YYYY-MM-DD'),
                              'notes', (ARRAY['Discutir a montagem e o ritmo.', 'Trechos para a prova.', 'Comparar com o livro.', NULL])[1 + (i % 4)],
                              'turmas', (SELECT COALESCE(jsonb_agg(discipline || ' (' || code || ')'), '[]'::jsonb) FROM turmas WHERE id = ANY(turma_ids))),
           CASE WHEN d < (NOW() AT TIME ZONE 'America/Fortaleza')::date THEN 'approved' WHEN i % 3 = 0 THEN 'analysis' WHEN i % 7 = 0 THEN 'refused' ELSE 'approved' END,
           CASE WHEN i % 7 = 0 THEN 'Sem cópia livre nessa data; sugerimos outra semana.' WHEN i % 3 <> 0 THEN 'Cópia separada para a aula.' END,
           (d - 7)::timestamp AT TIME ZONE 'America/Fortaleza', COALESCE(turma_ids, '{}')
      FROM films fl WHERE fl.id = fid;
  END LOOP;
  UPDATE professor_requests SET created_at = LEAST(created_at, NOW() - INTERVAL '1 hour');

  -- ── Equipamentos: pedidos e empréstimos ────────────────────────────
  FOR i IN 1..22 LOOP
    u := everyone[1 + floor(random()*array_length(everyone,1))::int];
    t_start := date_trunc('hour', NOW()) + make_interval(days => -40 + floor(random()*55)::int, hours => 2);
    INSERT INTO equipment_requests(user_id, equipment_id, quantity, pickup_at, return_at, purpose, notes, status, staff_notes, decided_by, decided_at, created_at)
    SELECT u, eq.id, 1 + (random() < 0.25)::int, t_start, t_start + make_interval(hours => 3 + floor(random()*48)::int),
           (ARRAY['Gravação de curta para a disciplina', 'Exibição em sala de aula', 'Trabalho acadêmico em grupo', 'Ensaio fotográfico do TCC', 'Apresentação de seminário'])[1 + (i % 5)],
           CASE WHEN i % 4 = 0 THEN 'Preciso do cabo HDMI junto.' END,
           st, CASE st WHEN 'approved' THEN 'Retire no balcão com a carteirinha.' WHEN 'refused' THEN 'Sem unidade livre nesse período.' END,
           CASE WHEN st IN ('approved','refused') THEN staff1 END, CASE WHEN st IN ('approved','refused') THEN t_start - INTERVAL '1 day' END,
           LEAST(t_start - INTERVAL '3 days', NOW() - INTERVAL '2 hours')
      FROM (SELECT id FROM equipment ORDER BY random() LIMIT 1) eq,
           LATERAL (SELECT CASE WHEN t_start < NOW() THEN (ARRAY['approved','approved','refused','cancelled'])[1 + floor(random()*4)::int]
                                ELSE (ARRAY['analysis','analysis','approved','refused'])[1 + floor(random()*4)::int] END AS st) s;
  END LOOP;
  -- Empréstimos de equipamento: histórico devolvido e alguns em uso
  FOR i IN 1..30 LOOP
    u := everyone[1 + floor(random()*array_length(everyone,1))::int];
    t_start := NOW() - make_interval(days => floor(random()*120)::int, hours => floor(random()*8)::int);
    INSERT INTO equipment_loans(equipment_id, user_id, quantity, checked_out_by, checked_in_by, borrowed_at, due_at, returned_at, condition, damaged_quantity, notes)
    SELECT eq.id, u, 1, staff1, CASE WHEN i > 4 THEN staff2 END, t_start, t_start + INTERVAL '2 days',
           CASE WHEN i > 4 THEN t_start + make_interval(hours => 4 + floor(random()*44)::int) END,
           CASE WHEN i > 4 THEN CASE WHEN i % 13 = 0 THEN 'damaged' ELSE 'good' END END,
           CASE WHEN i > 4 AND i % 13 = 0 THEN 1 ELSE 0 END,
           CASE WHEN i % 13 = 0 THEN 'Lente com arranhão' END
      FROM (SELECT id FROM equipment ORDER BY random() LIMIT 1) eq;
  END LOOP;
  UPDATE equipment_loans SET borrowed_at = NOW() - INTERVAL '6 hours', due_at = NOW() + INTERVAL '1 day' WHERE returned_at IS NULL AND id IN (SELECT id FROM equipment_loans WHERE returned_at IS NULL ORDER BY id LIMIT 3);

  -- ── Sugestões de título ────────────────────────────────────────────
  INSERT INTO film_suggestions(user_id, title, year, director, project, reason, status, staff_notes, decided_at, created_at)
  SELECT s.u, s.title, s.year, s.director, s.project, s.reason, s.status, s.notes,
         CASE WHEN s.status <> 'new' THEN NOW() - INTERVAL '3 days' END, NOW() - make_interval(days => s.ago)
  FROM (VALUES
    (aluno_ids[2], 'Marte Um', 2022, 'Gabriel Martins', 'bibcine', 'Cinema mineiro independente que conversa com a realidade da periferia.', 'new', NULL, 4),
    (aluno_ids[5], 'Marte Um', 2022, 'Gabriel Martins', 'bibcine', 'Filme que representou o Brasil no Oscar; ótimo para debate.', 'new', NULL, 2),
    (prof_ids[2],  'Marte Um', 2022, 'Gabriel Martins', 'bibcine', 'Uso em Linguagem Cinematográfica.', 'new', NULL, 1),
    (aluno_ids[3], 'O Auto da Compadecida', 2000, 'Guel Arraes', 'bibcine', 'Clássico nordestino que todo mundo gosta de rever.', 'approved', 'Entra no BibCine do mês que vem, na Sala C.', 20),
    (aluno_ids[7], 'Bacurau', 2019, 'Kleber Mendonça Filho', 'bibcine', 'Gostaria de uma sessão com debate sobre o sertão.', 'approved', 'Já temos no acervo; marcamos sessão.', 30),
    (aluno_ids[4], 'Sintonia', 2019, NULL, 'vai_de_serie', 'Série sobre juventude periférica, ótima para conversa.', 'approved', 'Na programação do Vai de Série.', 25),
    (aluno_ids[6], 'Cangaço Novo', 2023, NULL, 'vai_de_serie', 'Série cearense filmada no sertão.', 'new', NULL, 3),
    (aluno_ids[8], 'Cangaço Novo', 2023, NULL, 'vai_de_serie', 'Produção nordestina recente.', 'new', NULL, 1),
    (aluno_ids[9], 'Velozes e Furiosos 9', 2021, 'Justin Lin', 'bibcine', 'Para uma sessão mais leve.', 'rejected', 'Fora da proposta do BibCine (cinema de autor e acervo).', 12),
    (prof_ids[3],  'Lavoura Arcaica', 2001, 'Luiz Fernando Carvalho', 'bibcine', 'Adaptação de Raduan Nassar para Literatura e Cinema.', 'approved', 'Sessão marcada.', 40),
    (aluno_ids[10], 'Cidade Invisível', 2021, NULL, 'vai_de_serie', 'Folclore brasileiro em série.', 'new', NULL, 5),
    (prof_ids[4],  'Ainda Estou Aqui', 2024, 'Walter Salles', 'bibcine', 'Para História do Brasil Contemporâneo.', 'approved', 'Já exibido; pode reservar a cópia.', 45)
  ) AS s(u, title, year, director, project, reason, status, notes, ago);

  -- ── Fale com a Videoteca (SAC) ─────────────────────────────────────
  FOR e IN SELECT * FROM (VALUES
      (aluno_ids[2], 'Cópia com defeito', 'CP-0002', 'O disco trava no capítulo 12.', 'answered', 'Obrigada pelo aviso! A cópia foi para revisão e separamos outra para você.', NULL::text, 6),
      (aluno_ids[3], 'Reservas e empréstimos', NULL, 'Posso renovar um filme que já está atrasado?', 'closed', 'Não: com atraso a renovação fica bloqueada. Devolva no balcão e reserve de novo.', 'Entendi, obrigado.', 15),
      (aluno_ids[4], 'Salas', 'Sala B', 'A Sala B tem tomada perto da tela para notebook?', 'open', NULL, NULL, 1),
      (prof_ids[2],  'Equipamentos', 'EQ-0004', 'O projetor portátil tem entrada USB-C?', 'answered', 'Tem só HDMI; emprestamos o adaptador junto.', NULL, 3),
      (aluno_ids[6], 'Acesso ao sistema', NULL, 'Não recebo os e-mails de aviso de devolução.', 'open', NULL, NULL, 0),
      (aluno_ids[8], 'Outro assunto', NULL, 'Vocês aceitam doação de DVDs?', 'answered', 'Aceitamos! Traga ao balcão de seg. a sex.; a equipe avalia o estado das mídias.', NULL, 9),
      (prof_ids[3],  'Reservas e empréstimos', 'Lavoura Arcaica', 'Preciso da cópia por 15 dias para a disciplina. É possível?', 'closed', 'Sim, ajustamos o prazo no balcão para 15 dias.', 'Perfeito, obrigado!', 20),
      (aluno_ids[10], 'Cópia com defeito', 'Bacurau', 'A legenda está fora de sincronia.', 'open', NULL, NULL, 2)
    ) AS t(u, topic, ref, msg, status, reply, follow, ago)
  LOOP
    INSERT INTO contact_tickets(user_id, topic, reference, status, created_at, updated_at)
      VALUES (e.u, e.topic, e.ref, e.status, NOW() - make_interval(days => e.ago, hours => 3), NOW() - make_interval(days => e.ago))
      RETURNING id INTO tid;
    INSERT INTO contact_messages(ticket_id, author_id, from_staff, body, created_at) VALUES (tid, e.u, FALSE, e.msg, NOW() - make_interval(days => e.ago, hours => 3));
    IF e.reply IS NOT NULL THEN
      INSERT INTO contact_messages(ticket_id, author_id, from_staff, body, created_at) VALUES (tid, staff2, TRUE, e.reply, NOW() - make_interval(days => e.ago, hours => 1));
    END IF;
    IF e.follow IS NOT NULL THEN
      INSERT INTO contact_messages(ticket_id, author_id, from_staff, body, created_at) VALUES (tid, e.u, FALSE, e.follow, NOW() - make_interval(days => e.ago));
    END IF;
  END LOOP;

  -- ── Direitos LGPD ──────────────────────────────────────────────────
  INSERT INTO data_requests(user_id, kind, status, created_at) VALUES (aluno_ids[9], 'export', 'pending', NOW() - INTERVAL '1 day');
  INSERT INTO data_requests(user_id, kind, status, created_at, resolved_at) VALUES (aluno_ids[6], 'export', 'done', NOW() - INTERVAL '20 days', NOW() - INTERVAL '18 days');

  -- ── Notificações coerentes com o que aconteceu ─────────────────────
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT l.user_id, 'Devolução em atraso: ' || fl.title, 'O prazo venceu em ' || to_char(l.due_at AT TIME ZONE 'America/Fortaleza', 'DD/MM') || '. Devolva no balcão da Videoteca.', FALSE, l.due_at + INTERVAL '1 day'
      FROM loans l JOIN copies c5 ON c5.id = l.copy_id JOIN films fl ON fl.id = c5.film_id WHERE l.returned_at IS NULL AND l.due_at < NOW();
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT l.user_id, 'Devolução em 2 dias: ' || fl.title, 'Devolva até ' || to_char(l.due_at AT TIME ZONE 'America/Fortaleza', 'DD/MM') || ' ou renove em Minha videoteca.', random() < 0.4, l.due_at - INTERVAL '2 days'
      FROM loans l JOIN copies c5 ON c5.id = l.copy_id JOIN films fl ON fl.id = c5.film_id WHERE l.returned_at IS NULL AND l.due_at >= NOW() AND l.due_at - INTERVAL '2 days' < NOW();
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT r.user_id, 'Reserva pronta para retirada', 'Sua reserva de "' || fl.title || '" está disponível até ' || to_char(r.pickup_until AT TIME ZONE 'America/Fortaleza', 'DD/MM') || '.', FALSE, r.requested_at + INTERVAL '1 day'
      FROM reservations r JOIN films fl ON fl.id = r.film_id WHERE r.status = 'ready';
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT pr.professor_id, CASE pr.status WHEN 'approved' THEN 'Pedido aprovado' ELSE 'Pedido recusado' END || ': ' || COALESCE(pr.payload->>'room_name', pr.payload->>'film_title'),
           COALESCE(pr.staff_notes, ''), pr.created_at < NOW() - INTERVAL '3 days', pr.created_at + INTERVAL '1 day'
      FROM professor_requests pr WHERE pr.status IN ('approved','refused') AND pr.created_at + INTERVAL '1 day' < NOW() AND pr.created_at > NOW() - INTERVAL '20 days';
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT r.user_id, 'Lembrete: ' || ev.title, 'A sessão começa amanhã às ' || to_char(ev.starts_at AT TIME ZONE 'America/Fortaleza', 'HH24:MI') || ', na ' || ev.location || '.', FALSE, LEAST(ev.starts_at - INTERVAL '1 day', NOW())
      FROM event_registrations r JOIN events ev ON ev.id = r.event_id WHERE ev.status = 'published' AND ev.starts_at BETWEEN NOW() AND NOW() + INTERVAL '2 days';
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT t.user_id, 'Resposta da Videoteca: ' || t.topic, 'Protocolo SAC-' || extract(year FROM t.created_at) || '-' || lpad(t.id::text, 4, '0') || '. Veja em Fale com a Videoteca.', t.status = 'closed', t.updated_at
      FROM contact_tickets t WHERE t.status IN ('answered','closed');
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT s.user_id, 'Sua sugestão foi aprovada: ' || s.title, COALESCE(s.staff_notes, ''), FALSE, s.decided_at
      FROM film_suggestions s WHERE s.status = 'approved';
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT x, 'Nova sessão do Vai de Série', 'Episódios de série com conversa às quartas, 16h, na Sala B. Veja a Programação.', random() < 0.5, NOW() - INTERVAL '4 days'
      FROM unnest(aluno_ids) x;

  -- Avisos para a equipe
  INSERT INTO notifications(user_id, title, body, read, created_at)
    SELECT s.id, x.title, x.body, x.lida, NOW() - make_interval(hours => x.h)
      FROM profiles s CROSS JOIN (VALUES
        ('Novos pedidos aguardando decisão', 'Há pedidos de sala, filme e equipamento em Pedidos.', FALSE, 2),
        ('Nova mensagem no atendimento', 'Um aluno escreveu em Fale com a Videoteca.', FALSE, 5),
        ('Empréstimos em atraso', 'Há empréstimos vencidos; os alunos já foram avisados.', TRUE, 26),
        ('Sugestão repetida: Marte Um', 'Três pessoas sugeriram o mesmo título para o BibCine.', FALSE, 30)
      ) AS x(title, body, lida, h)
     WHERE s.role IN ('funcionario', 'admin');

  RAISE NOTICE 'seed_zzz_demo_completo: aplicado';
END $$;
