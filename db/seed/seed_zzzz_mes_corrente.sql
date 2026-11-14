-- Videoteca Unifor — Movimento dia a dia de 60 dias atrás até 60 dias à frente (DEV/hml).
-- NÃO usar em produção. Roda depois de seed_zzz_demo_completo.sql. Em cada dia útil da janela:
-- empréstimos e devoluções, reservas, equipamentos (dias passados), aulas e pedidos de sala,
-- pedidos de equipamento (dias futuros) e uma sessão do BibCine às 19h na Sala C.
-- Idempotente: um dia que já tem sessão 'Mostra do período' (ou 'Mostra do mês', da versão
-- anterior) é pulado; rodar de novo não duplica nada.

DO $$
DECLARE
  staff1 uuid := '33333333-3333-3333-3333-333333333333';
  staff2 uuid;
  everyone uuid[];
  aluno_ids uuid[];
  prof_ids uuid[];
  films_ok bigint[];
  first_day date := (NOW() AT TIME ZONE 'America/Fortaleza')::date - 60;
  last_day date := (NOW() AT TIME ZONE 'America/Fortaleza')::date + 60;
  today date := (NOW() AT TIME ZONE 'America/Fortaleza')::date;
  d date; u uuid; p uuid; fid bigint; cid bigint; bid bigint; eid bigint; rid bigint;
  t_start timestamptz; t_end timestamptz; j int; n int; k int;
  f record;
  turma_ids bigint[];
  slots CONSTANT text[][] := ARRAY[['07:30','09:10'],['09:20','11:00'],['11:20','13:00'],['13:30','15:10'],['15:30','17:10'],['18:10','19:50']];
BEGIN
  PERFORM setseed(0.11);
  SELECT id INTO staff2 FROM profiles WHERE registration = 'FUNC002';
  staff2 := COALESCE(staff2, staff1);
  SELECT array_agg(id) INTO everyone FROM profiles WHERE role IN ('aluno', 'professor');
  SELECT array_agg(id) INTO aluno_ids FROM profiles WHERE role = 'aluno';
  SELECT array_agg(id) INTO prof_ids FROM profiles WHERE role = 'professor';
  SELECT array_agg(id) INTO films_ok FROM films
   WHERE status = 'available' AND NOT local_only
     AND (rights_status = 'public_domain' OR (rights_status = 'authorized' AND EXISTS (SELECT 1 FROM film_rights_proofs pr WHERE pr.film_id = films.id)))
     AND (rights_valid_until IS NULL OR rights_valid_until > last_day);

  d := first_day;
  WHILE d <= last_day LOOP
    IF extract(isodow FROM d) <= 5 AND NOT EXISTS (
         SELECT 1 FROM events ev WHERE (ev.description LIKE 'Mostra do período%' OR ev.description LIKE 'Mostra do mês%')
           AND (ev.starts_at AT TIME ZONE 'America/Fortaleza')::date = d) THEN

      -- ── Empréstimos (dias passados): 3 a 6 por dia, quase todos já devolvidos ──
      IF d < today THEN
        FOR j IN 1..(3 + floor(random()*4)::int) LOOP
          u := everyone[1 + floor(random()*array_length(everyone,1))::int];
          SELECT c.id INTO cid FROM copies c JOIN films fl ON fl.id = c.film_id
           WHERE c.status = 'available' AND NOT fl.local_only ORDER BY random() LIMIT 1;
          CONTINUE WHEN cid IS NULL;
          t_start := (d + make_interval(hours => 8 + floor(random()*11)::int, mins => floor(random()*60)::int)) AT TIME ZONE 'America/Fortaleza';
          CONTINUE WHEN EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = cid AND tstzrange(l.borrowed_at, COALESCE(l.returned_at, 'infinity')) && tstzrange(t_start, t_start + INTERVAL '8 days'));
          IF t_start + INTERVAL '6 days' < NOW() OR random() < 0.4 THEN
            INSERT INTO loans(user_id, copy_id, borrowed_at, due_at, returned_at, status, renewals, return_condition)
            VALUES (u, cid, t_start, t_start + INTERVAL '7 days', LEAST(t_start + make_interval(days => 1 + floor(random()*6)::int), NOW() - INTERVAL '1 hour'), 'returned', 0, 'good');
          END IF;
        END LOOP;

        -- Reservas atendidas no dia
        FOR j IN 1..(1 + floor(random()*2)::int) LOOP
          INSERT INTO reservations(user_id, film_id, status, requested_at)
          VALUES (aluno_ids[1 + floor(random()*array_length(aluno_ids,1))::int], films_ok[1 + floor(random()*array_length(films_ok,1))::int],
                  (ARRAY['fulfilled','fulfilled','cancelled','expired'])[1 + floor(random()*4)::int],
                  (d + make_interval(hours => 9 + j)) AT TIME ZONE 'America/Fortaleza');
        END LOOP;

        -- Equipamentos emprestados e devolvidos no dia
        FOR j IN 1..(1 + floor(random()*2)::int) LOOP
          t_start := (d + make_interval(hours => 8 + floor(random()*10)::int)) AT TIME ZONE 'America/Fortaleza';
          INSERT INTO equipment_loans(equipment_id, user_id, quantity, checked_out_by, checked_in_by, borrowed_at, due_at, returned_at, condition, damaged_quantity)
          SELECT e.id, everyone[1 + floor(random()*array_length(everyone,1))::int], 1, staff1, staff2, t_start, t_start + INTERVAL '1 day',
                 LEAST(t_start + make_interval(hours => 3 + floor(random()*20)::int), NOW() - INTERVAL '1 hour'), 'good', 0
            FROM (SELECT id FROM equipment ORDER BY random() LIMIT 1) e;
        END LOOP;
      END IF;

      -- ── Aulas e pedidos de sala: 2 a 4 por dia útil nas Salas A e B ──
      FOR j IN 1..(2 + floor(random()*3)::int) LOOP
        p := prof_ids[1 + floor(random()*array_length(prof_ids,1))::int];
        k := 1 + floor(random()*6)::int;
        rid := 1 + (random() < 0.5)::int;
        t_start := (d + slots[k][1]::time) AT TIME ZONE 'America/Fortaleza';
        t_end := (d + slots[k][2]::time) AT TIME ZONE 'America/Fortaleza';
        CONTINUE WHEN EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = rid AND b.status <> 'cancelled' AND tstzrange(b.starts_at, b.ends_at) && tstzrange(t_start, t_end));
        SELECT array_agg(id) INTO turma_ids FROM (SELECT id FROM turmas WHERE professor_id = p ORDER BY random() LIMIT 1) s;
        n := CASE WHEN d < today THEN 1 ELSE floor(random()*10)::int END;
        INSERT INTO room_bookings(room_id, professor_id, starts_at, ends_at, title, status, course, registration, student_count)
        SELECT rid, p, t_start, t_end,
               (ARRAY['Exibição e debate', 'Análise de cenas', 'Aula com trechos de filmes', 'Seminário da turma', 'Mostra de curtas da turma'])[1 + floor(random()*5)::int],
               CASE WHEN n <= 6 THEN 'confirmed' WHEN n <= 8 THEN 'pending' ELSE 'cancelled' END,
               COALESCE((SELECT string_agg(discipline || ' (' || code || ')', ', ') FROM turmas WHERE id = ANY(turma_ids)), pr.course, 'Aula'),
               pr.registration, 12 + floor(random()*25)::int
          FROM profiles pr WHERE pr.id = p
        RETURNING id INTO bid;
        INSERT INTO professor_requests(professor_id, request_type, payload, status, staff_notes, created_at, booking_id, turma_ids)
        SELECT p, 'room',
               jsonb_build_object('room_id', b.room_id, 'room_name', r.name, 'title', b.title, 'course', b.course, 'registration', b.registration,
                                  'student_count', b.student_count,
                                  'starts_at', to_char(b.starts_at AT TIME ZONE 'America/Fortaleza', 'YYYY-MM-DD"T"HH24:MI:SS') || '-03:00',
                                  'ends_at', to_char(b.ends_at AT TIME ZONE 'America/Fortaleza', 'YYYY-MM-DD"T"HH24:MI:SS') || '-03:00', 'notes', NULL,
                                  'turma_ids', to_jsonb(COALESCE(turma_ids, '{}')),
                                  'turmas', (SELECT COALESCE(jsonb_agg(discipline || ' (' || code || ')'), '[]'::jsonb) FROM turmas WHERE id = ANY(turma_ids))),
               CASE b.status WHEN 'confirmed' THEN 'approved' WHEN 'pending' THEN 'analysis' ELSE 'refused' END,
               CASE b.status WHEN 'confirmed' THEN 'Sala reservada.' WHEN 'cancelled' THEN 'Horário ocupado por manutenção.' END,
               LEAST(b.starts_at - INTERVAL '4 days', NOW() - INTERVAL '1 hour'), b.id, COALESCE(turma_ids, '{}')
          FROM room_bookings b JOIN rooms r ON r.id = b.room_id WHERE b.id = bid;
      END LOOP;

      -- ── Pedidos de equipamento para os próximos dias ──
      IF d >= today AND random() < 0.7 THEN
        t_start := (d + TIME '13:00') AT TIME ZONE 'America/Fortaleza';
        INSERT INTO equipment_requests(user_id, equipment_id, quantity, pickup_at, return_at, purpose, status, staff_notes, decided_by, decided_at, created_at)
        SELECT everyone[1 + floor(random()*array_length(everyone,1))::int], e.id, 1, t_start, t_start + INTERVAL '5 hours',
               (ARRAY['Gravação de curta', 'Exibição em sala de aula', 'Trabalho em grupo', 'Apresentação de seminário'])[1 + floor(random()*4)::int],
               st, CASE WHEN st = 'approved' THEN 'Retire no balcão com a carteirinha.' END,
               CASE WHEN st = 'approved' THEN staff1 END, CASE WHEN st = 'approved' THEN NOW() - INTERVAL '2 hours' END, NOW() - INTERVAL '1 day'
          FROM (SELECT id FROM equipment ORDER BY random() LIMIT 1) e,
               LATERAL (SELECT (ARRAY['analysis','analysis','approved'])[1 + floor(random()*3)::int] AS st) s;
      END IF;

      -- ── BibCine: sessões das 19h na Sala C nos dias que ainda não têm ──
      t_start := (d + TIME '19:00') AT TIME ZONE 'America/Fortaleza';
      t_end := (d + TIME '21:20') AT TIME ZONE 'America/Fortaleza';
      IF EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = 3 AND b.status <> 'cancelled' AND tstzrange(b.starts_at, b.ends_at) && tstzrange(t_start, t_end))
         OR EXISTS (SELECT 1 FROM events ev WHERE ev.room_id = 3 AND ev.status IN ('published','finished') AND tstzrange(ev.starts_at, ev.ends_at) && tstzrange(t_start, t_end)) THEN
        t_start := (d + TIME '16:30') AT TIME ZONE 'America/Fortaleza';
        t_end := (d + TIME '18:50') AT TIME ZONE 'America/Fortaleza';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = 3 AND b.status <> 'cancelled' AND tstzrange(b.starts_at, b.ends_at) && tstzrange(t_start, t_end))
         AND NOT EXISTS (SELECT 1 FROM events ev WHERE ev.room_id = 3 AND ev.status IN ('published','finished') AND tstzrange(ev.starts_at, ev.ends_at) && tstzrange(t_start, t_end)) THEN
        fid := films_ok[1 + floor(random()*array_length(films_ok,1))::int];
        SELECT title INTO f FROM films WHERE id = fid;
        INSERT INTO events(title, event_type, film_id, location, starts_at, ends_at, slots, description, certificate, status, room_id)
        VALUES ('BibCine: ' || f.title, 'exibicao', fid, 'Sala C', t_start, t_end, 80,
                'Mostra do período: exibição seguida de debate.', TRUE, CASE WHEN t_end < NOW() THEN 'finished' ELSE 'published' END, 3)
        RETURNING id INTO eid;
        INSERT INTO event_registrations(event_id, user_id, present)
          SELECT eid, x, t_end < NOW() AND random() < 0.8 FROM unnest(everyone) x WHERE random() < (CASE WHEN t_end < NOW() THEN 0.5 ELSE 0.25 END)
          ON CONFLICT DO NOTHING;
      END IF;
    END IF;
    d := d + 1;
  END LOOP;

  RAISE NOTICE 'seed_zzzz_mes_corrente: janela de 120 dias aplicada';
END $$;
