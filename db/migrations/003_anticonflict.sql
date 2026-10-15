-- Videoteca Unifor — Migration 003
-- Regra anticonflito de reserva de sala (RF03 / T06.4).
-- Impede que duas reservas ativas ocupem a MESMA sala em horários sobrepostos,
-- garantido no nível do banco via constraint de exclusão (GiST).
-- Idempotente.

-- btree_gist permite usar o operador de igualdade (=) em room_id dentro de
-- uma constraint GiST junto do operador de sobreposição (&&) de intervalos.
CREATE EXTENSION IF NOT EXISTS btree_gist;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'room_bookings_no_overlap' AND conrelid = 'room_bookings'::regclass
  ) THEN
    ALTER TABLE room_bookings ADD CONSTRAINT room_bookings_no_overlap
      EXCLUDE USING gist (
        room_id WITH =,
        tstzrange(starts_at, ends_at) WITH &&
      )
      WHERE (status <> 'cancelled');
  END IF;
END $$;

COMMENT ON CONSTRAINT room_bookings_no_overlap ON room_bookings IS
  'Anticonflito (T06.4): nenhuma sala pode ter duas reservas não-canceladas com horários sobrepostos.';
