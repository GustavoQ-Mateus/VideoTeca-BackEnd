"""Turmas (migration 017) e contato da Videoteca.

- Professor escolhe as turmas ao pedir sala ou filme para a aula.
- A aula aprovada aparece só para os alunos dessas turmas (fora do catálogo e da Programação).
- A equipe cadastra turmas e matricula alunos; também edita o contato exibido aos usuários.
"""

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import require_role
from app.db.postgres import get_pool

router = APIRouter()
staff = require_role("funcionario", "admin")
member = require_role("aluno", "professor")

TURMA_SELECT = """
SELECT t.id, t.code, t.discipline, t.semester, t.professor_id, p.full_name AS professor_name, p.registration AS professor_registration,
  (SELECT COUNT(*) FROM turma_alunos a WHERE a.turma_id = t.id) AS student_count
FROM turmas t JOIN profiles p ON p.id = t.professor_id
"""


def label(t) -> str:
    return f"{t['discipline']} ({t['code']})"


async def resolve_turmas(conn, user, ids: list[int]) -> list[dict]:
    """Confere se as turmas são do professor autenticado. Aluno não informa turma."""
    ids = sorted(set(ids))
    if not ids:
        return []
    if user["role"] != "professor":
        raise HTTPException(422, "Só professores informam turmas")
    rows = await conn.fetch("SELECT id, code, discipline FROM turmas WHERE id = ANY($1::bigint[]) AND professor_id = $2", ids, user["id"])
    if len(rows) != len(ids):
        raise HTTPException(422, "Turma inválida para este professor")
    return [dict(r) for r in rows]


@router.get("/turmas/me")
async def my_turmas(user=Depends(member)):
    if user["role"] == "professor":
        rows = await get_pool().fetch(TURMA_SELECT + " WHERE t.professor_id = $1 ORDER BY t.discipline, t.code", user["id"])
    else:
        rows = await get_pool().fetch(
            TURMA_SELECT + " WHERE t.id IN (SELECT turma_id FROM turma_alunos WHERE user_id = $1) ORDER BY t.discipline, t.code", user["id"]
        )
    return [dict(r) | {"label": label(r)} for r in rows]


@router.get("/aulas/me")
async def my_classes(user=Depends(require_role("aluno"))):
    """Aulas aprovadas das turmas do aluno, a partir de hoje."""
    rows = await get_pool().fetch(
        """
        SELECT r.id, r.request_type, r.payload, p.full_name AS professor_name,
          ARRAY(SELECT t.discipline || ' (' || t.code || ')' FROM turmas t
                WHERE t.id = ANY(r.turma_ids) AND t.id IN (SELECT turma_id FROM turma_alunos WHERE user_id = $1)) AS turmas
        FROM professor_requests r JOIN profiles p ON p.id = r.professor_id
        WHERE r.status = 'approved'
          AND r.turma_ids && ARRAY(SELECT turma_id FROM turma_alunos WHERE user_id = $1)
        """,
        user["id"],
    )
    out = []
    for r in rows:
        p = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
        when = p.get("starts_at") or (f"{p['date']}T19:00:00-03:00" if p.get("date") else None)
        out.append({
            "id": r["id"], "kind": r["request_type"], "title": p.get("title") or p.get("film_title"),
            "film_id": p.get("film_id"), "film_title": p.get("film_title"), "room_name": p.get("room_name"),
            "starts_at": p.get("starts_at"), "ends_at": p.get("ends_at"), "date": p.get("date"),
            "professor_name": r["professor_name"], "turmas": list(r["turmas"]), "_when": when,
        })
    today = date.today().isoformat()
    out = [a for a in out if (a["_when"] or "")[:10] >= today]
    out.sort(key=lambda a: a["_when"] or "")
    for a in out:
        a.pop("_when")
    return out


# ── Gestão de turmas (equipe) ────────────────────────────────────────

class TurmaIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=20)
    discipline: str = Field(min_length=2, max_length=160)
    semester: str = Field(default="2026.2", min_length=4, max_length=10)
    professor_registration: str = Field(min_length=1, max_length=40)


class StudentIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    registration: str = Field(min_length=1, max_length=40)


@router.get("/staff/turmas")
async def staff_turmas(user=Depends(staff)):
    rows = await get_pool().fetch(TURMA_SELECT + " ORDER BY t.semester DESC, t.discipline, t.code")
    return [dict(r) | {"label": label(r)} for r in rows]


@router.post("/staff/turmas", status_code=201)
async def create_turma(body: TurmaIn, user=Depends(staff)):
    pool = get_pool()
    prof = await pool.fetchrow("SELECT id FROM profiles WHERE registration = $1 AND role = 'professor'", body.professor_registration)
    if not prof:
        raise HTTPException(404, f"Nenhum professor com a matrícula {body.professor_registration}")
    exists = await pool.fetchval("SELECT 1 FROM turmas WHERE discipline = $1 AND code = $2 AND semester = $3", body.discipline, body.code, body.semester)
    if exists:
        raise HTTPException(409, "Essa turma já existe neste semestre")
    tid = await pool.fetchval(
        "INSERT INTO turmas(code, discipline, semester, professor_id) VALUES($1,$2,$3,$4) RETURNING id",
        body.code, body.discipline, body.semester, prof["id"],
    )
    row = await pool.fetchrow(TURMA_SELECT + " WHERE t.id = $1", tid)
    return dict(row) | {"label": label(row)}


@router.delete("/staff/turmas/{turma_id}", status_code=204)
async def delete_turma(turma_id: int, user=Depends(staff)):
    done = await get_pool().execute("DELETE FROM turmas WHERE id = $1", turma_id)
    if done.endswith(" 0"):
        raise HTTPException(404, "Turma não encontrada")


@router.get("/staff/turmas/{turma_id}/alunos")
async def turma_students(turma_id: int, user=Depends(staff)):
    rows = await get_pool().fetch(
        "SELECT p.id AS user_id, p.full_name AS name, p.registration, p.course FROM turma_alunos a "
        "JOIN profiles p ON p.id = a.user_id WHERE a.turma_id = $1 ORDER BY p.full_name",
        turma_id,
    )
    return [dict(r) for r in rows]


@router.post("/staff/turmas/{turma_id}/alunos", status_code=201)
async def add_student(turma_id: int, body: StudentIn, user=Depends(staff)):
    pool = get_pool()
    if not await pool.fetchval("SELECT 1 FROM turmas WHERE id = $1", turma_id):
        raise HTTPException(404, "Turma não encontrada")
    st = await pool.fetchrow("SELECT id, full_name, registration, course FROM profiles WHERE registration = $1 AND role = 'aluno'", body.registration)
    if not st:
        raise HTTPException(404, f"Nenhum aluno com a matrícula {body.registration}")
    await pool.execute("INSERT INTO turma_alunos(turma_id, user_id) VALUES($1,$2) ON CONFLICT DO NOTHING", turma_id, st["id"])
    return {"user_id": st["id"], "name": st["full_name"], "registration": st["registration"], "course": st["course"]}


@router.delete("/staff/turmas/{turma_id}/alunos/{user_id}", status_code=204)
async def remove_student(turma_id: int, user_id: str, user=Depends(staff)):
    await get_pool().execute("DELETE FROM turma_alunos WHERE turma_id = $1 AND user_id = $2::uuid", turma_id, user_id)


# ── Contato exibido em "Fale com a Videoteca" ────────────────────────

class ContactInfo(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    contact_location: str = Field(min_length=3, max_length=200)
    contact_hours: str = Field(min_length=3, max_length=200)
    contact_phones: str = Field(min_length=3, max_length=120)
    contact_email: str = Field(min_length=3, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    notice: Optional[str] = Field(default=None, max_length=500)


CONTACT_COLS = "contact_location, contact_hours, contact_phones, contact_email, notice"


@router.get("/contact/info")
async def contact_info():
    """Público: aparece no rodapé do site e em Fale com a Videoteca."""
    return dict(await get_pool().fetchrow(f"SELECT {CONTACT_COLS} FROM app_settings WHERE id = 1"))


@router.put("/staff/contact/info")
async def update_contact_info(body: ContactInfo, user=Depends(staff)):
    notice = body.notice or None
    row = await get_pool().fetchrow(
        f"UPDATE app_settings SET contact_location=$1, contact_hours=$2, contact_phones=$3, contact_email=$4, notice=$5, updated_at=NOW() "
        f"WHERE id = 1 RETURNING {CONTACT_COLS}",
        body.contact_location, body.contact_hours, body.contact_phones, body.contact_email, notice,
    )
    return dict(row)
