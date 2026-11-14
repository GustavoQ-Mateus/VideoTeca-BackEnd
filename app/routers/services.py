"""Serviços pedidos pela Videoteca (migration 016).

- Regras de empréstimo por filme (prazo, renovações, retirada, fila, só uso local).
- Pedido de equipamento por aluno/professor, decidido pela equipe.
- Sugestões de título para o BibCine e o Vai de Série.
- Atendimento (SAC): protocolo, conversa e resposta da equipe.
"""

import json
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.security import require_role
from app.db.postgres import get_pool
from app.notify import get_settings, notify

router = APIRouter()
staff = require_role("funcionario", "admin")
member = require_role("aluno", "professor")
anyone = require_role("aluno", "professor", "funcionario", "admin")


def protocol(ticket_id: int, created_at: datetime) -> str:
    return f"SAC-{created_at.year}-{ticket_id:04d}"


# ── Regras por filme ─────────────────────────────────────────────────

class FilmRules(BaseModel):
    loan_days: Optional[int] = Field(default=None, ge=1, le=60)
    max_renewals: Optional[int] = Field(default=None, ge=0, le=5)
    pickup_days: Optional[int] = Field(default=None, ge=1, le=15)
    queue_limit: Optional[int] = Field(default=None, ge=1, le=50)
    local_only: bool = False


RULE_COLS = "loan_days, max_renewals, pickup_days, queue_limit, local_only"


@router.get("/staff/films/{film_id}/rules")
async def get_rules(film_id: int, user=Depends(staff)):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(f"SELECT {RULE_COLS} FROM films WHERE id = $1", film_id)
        if not row:
            raise HTTPException(404, "Filme não encontrado")
        s = await get_settings(conn)
    return dict(row) | {"defaults": {"loan_days": s["loan_days"], "max_renewals": 1, "pickup_days": s["reservation_pickup_days"]}}


@router.put("/staff/films/{film_id}/rules")
async def put_rules(film_id: int, body: FilmRules, user=Depends(staff)):
    row = await get_pool().fetchrow(
        f"UPDATE films SET loan_days=$2, max_renewals=$3, pickup_days=$4, queue_limit=$5, local_only=$6 "
        f"WHERE id=$1 RETURNING {RULE_COLS}",
        film_id, body.loan_days, body.max_renewals, body.pickup_days, body.queue_limit, body.local_only,
    )
    if not row:
        raise HTTPException(404, "Filme não encontrado")
    return dict(row)


# ── Equipamentos: pedido do usuário ──────────────────────────────────

AVAILABLE_SQL = """
SELECT e.id, e.name, e.category, e.quantity,
  GREATEST(e.quantity - e.damaged_quantity
    - COALESCE((SELECT SUM(l.quantity) FROM equipment_loans l WHERE l.equipment_id = e.id AND l.returned_at IS NULL), 0), 0) AS available
FROM equipment e ORDER BY e.category, e.name
"""


@router.get("/equipment/catalog")
async def equipment_catalog(user=Depends(anyone)):
    return [dict(r) for r in await get_pool().fetch(AVAILABLE_SQL)]


class RequestItem(BaseModel):
    equipment_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=50)


class EquipmentRequestIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    items: list[RequestItem] = Field(min_length=1, max_length=10)
    pickup_at: AwareDatetime
    return_at: AwareDatetime
    purpose: str = Field(min_length=2, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=1000)


REQ_SELECT = """
SELECT r.id, r.equipment_id, e.name AS equipment, e.category, r.quantity, r.pickup_at, r.return_at,
  r.purpose, r.notes, r.status, r.staff_notes, r.created_at, r.decided_at,
  p.full_name AS user_name, p.registration, p.role AS user_role
FROM equipment_requests r JOIN equipment e ON e.id = r.equipment_id JOIN profiles p ON p.id = r.user_id
"""


@router.post("/equipment/requests", status_code=201)
async def request_equipment(body: EquipmentRequestIn, user=Depends(member)):
    if body.pickup_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "Escolha uma retirada no futuro")
    if body.return_at <= body.pickup_at:
        raise HTTPException(422, "A devolução deve ser depois da retirada")
    if len({i.equipment_id for i in body.items}) != len(body.items):
        raise HTTPException(422, "Equipamento repetido no pedido")
    ids = []
    async with get_pool().acquire() as conn, conn.transaction():
        for item in body.items:
            eq = await conn.fetchrow("SELECT name, quantity - damaged_quantity AS usable FROM equipment WHERE id = $1", item.equipment_id)
            if not eq:
                raise HTTPException(404, "Equipamento não encontrado")
            if item.quantity > eq["usable"]:
                raise HTTPException(409, f"{eq['name']}: a Videoteca tem só {eq['usable']} em condição de uso")
            ids.append(await conn.fetchval(
                "INSERT INTO equipment_requests (user_id, equipment_id, quantity, pickup_at, return_at, purpose, notes) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
                user["id"], item.equipment_id, item.quantity, body.pickup_at, body.return_at, body.purpose, body.notes,
            ))
        rows = await conn.fetch(REQ_SELECT + " WHERE r.id = ANY($1::bigint[]) ORDER BY r.id", ids)
    return [dict(r) for r in rows]


@router.get("/equipment/requests/me")
async def my_equipment_requests(user=Depends(member)):
    return [dict(r) for r in await get_pool().fetch(REQ_SELECT + " WHERE r.user_id = $1 ORDER BY r.created_at DESC", user["id"])]


@router.delete("/equipment/requests/{request_id}", status_code=204)
async def cancel_equipment_request(request_id: int, user=Depends(member)):
    done = await get_pool().fetchval(
        "UPDATE equipment_requests SET status = 'cancelled' WHERE id = $1 AND user_id = $2 AND status = 'analysis' RETURNING id",
        request_id, user["id"],
    )
    if not done:
        raise HTTPException(409, "Só é possível cancelar pedidos que ainda estão em análise")


@router.get("/staff/equipment-requests")
async def staff_equipment_requests(status: Optional[Literal["analysis", "approved", "refused", "cancelled"]] = None, user=Depends(staff)):
    if status:
        rows = await get_pool().fetch(REQ_SELECT + " WHERE r.status = $1 ORDER BY r.pickup_at", status)
    else:
        rows = await get_pool().fetch(REQ_SELECT + " ORDER BY r.created_at DESC LIMIT 300")
    return [dict(r) for r in rows]


class Decision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: Literal["analysis", "approved", "refused"]
    staff_notes: Optional[str] = Field(default=None, max_length=2000)


@router.put("/staff/equipment-requests/{request_id}")
async def decide_equipment_request(request_id: int, body: Decision, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "UPDATE equipment_requests SET status=$2, staff_notes=COALESCE($3, staff_notes), decided_by=$4, "
            "decided_at = CASE WHEN $2 = 'analysis' THEN NULL ELSE NOW() END "
            "WHERE id=$1 AND status <> 'cancelled' RETURNING id, user_id",
            request_id, body.status, body.staff_notes, user["id"],
        )
        if not row:
            raise HTTPException(404, "Pedido não encontrado ou cancelado")
        if body.status != "analysis":
            name = await conn.fetchval("SELECT e.name FROM equipment_requests r JOIN equipment e ON e.id = r.equipment_id WHERE r.id = $1", request_id)
            await notify(
                conn, row["user_id"],
                f"Pedido de equipamento {'aprovado' if body.status == 'approved' else 'recusado'}: {name}",
                body.staff_notes or ("Retire no balcão no horário combinado." if body.status == "approved" else ""),
                "requests",
            )
        full = await conn.fetchrow(REQ_SELECT + " WHERE r.id = $1", request_id)
    return dict(full)


# ── Sugestões de título ──────────────────────────────────────────────

class SuggestionIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    year: Optional[int] = Field(default=None, ge=1880, le=2100)
    director: Optional[str] = Field(default=None, max_length=200)
    project: Literal["bibcine", "vai_de_serie"]
    reason: str = Field(min_length=3, max_length=2000)


SUG_SELECT = """
SELECT s.id, s.title, s.year, s.director, s.project, s.reason, s.status, s.staff_notes, s.created_at, s.decided_at,
  p.full_name AS user_name, p.registration,
  (SELECT COUNT(*) FROM film_suggestions o WHERE lower(o.title) = lower(s.title)) AS same_title,
  (SELECT f.id FROM films f WHERE lower(f.title) = lower(s.title) AND f.status <> 'inactive' LIMIT 1) AS film_id
FROM film_suggestions s JOIN profiles p ON p.id = s.user_id
"""


@router.post("/suggestions", status_code=201)
async def suggest(body: SuggestionIn, user=Depends(anyone)):
    pool = get_pool()
    dup = await pool.fetchval(
        "SELECT 1 FROM film_suggestions WHERE user_id = $1 AND lower(title) = lower($2) AND status = 'new'", user["id"], body.title
    )
    if dup:
        raise HTTPException(409, "Você já sugeriu este título e ele ainda está em avaliação")
    sid = await pool.fetchval(
        "INSERT INTO film_suggestions (user_id, title, year, director, project, reason) VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
        user["id"], body.title, body.year, body.director, body.project, body.reason,
    )
    return dict(await pool.fetchrow(SUG_SELECT + " WHERE s.id = $1", sid))


@router.get("/suggestions/me")
async def my_suggestions(user=Depends(anyone)):
    return [dict(r) for r in await get_pool().fetch(SUG_SELECT + " WHERE s.user_id = $1 ORDER BY s.created_at DESC", user["id"])]


@router.get("/staff/suggestions")
async def staff_suggestions(status: Optional[Literal["new", "approved", "rejected"]] = None, user=Depends(staff)):
    where, args = ("WHERE s.status = $1", [status]) if status else ("", [])
    return [dict(r) for r in await get_pool().fetch(SUG_SELECT + f" {where} ORDER BY s.created_at DESC LIMIT 300", *args)]


class SuggestionDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: Literal["new", "approved", "rejected"]
    staff_notes: Optional[str] = Field(default=None, max_length=2000)


@router.put("/staff/suggestions/{suggestion_id}")
async def decide_suggestion(suggestion_id: int, body: SuggestionDecision, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "UPDATE film_suggestions SET status=$2, staff_notes=COALESCE($3, staff_notes), "
            "decided_at = CASE WHEN $2 = 'new' THEN NULL ELSE NOW() END WHERE id=$1 RETURNING user_id, title",
            suggestion_id, body.status, body.staff_notes,
        )
        if not row:
            raise HTTPException(404, "Sugestão não encontrada")
        if body.status != "new":
            # Avisa todas as pessoas que sugeriram o mesmo título.
            people = await conn.fetch(
                "SELECT DISTINCT user_id FROM film_suggestions WHERE lower(title) = lower($1)", row["title"]
            )
            for p in people:
                await notify(
                    conn, p["user_id"],
                    f"Sua sugestão foi {'aprovada' if body.status == 'approved' else 'avaliada'}: {row['title']}",
                    body.staff_notes or "",
                    "events",
                )
            await conn.execute(
                "UPDATE film_suggestions SET status=$2, staff_notes=COALESCE($3, staff_notes), decided_at=NOW() "
                "WHERE lower(title) = lower($1) AND status = 'new'",
                row["title"], body.status, body.staff_notes,
            )
        full = await conn.fetchrow(SUG_SELECT + " WHERE s.id = $1", suggestion_id)
    return dict(full)


# ── Atendimento (SAC) ────────────────────────────────────────────────

class TicketIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    topic: str = Field(min_length=2, max_length=80)
    reference: Optional[str] = Field(default=None, max_length=200)
    message: str = Field(min_length=3, max_length=4000)


class MessageIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=4000)


class ReplyIn(MessageIn):
    close: bool = False


async def ticket_view(conn, ticket_id: int) -> dict:
    t = await conn.fetchrow(
        "SELECT t.*, p.full_name AS user_name, p.registration, p.role AS user_role "
        "FROM contact_tickets t JOIN profiles p ON p.id = t.user_id WHERE t.id = $1",
        ticket_id,
    )
    msgs = await conn.fetch(
        "SELECT m.id, m.body, m.from_staff, m.created_at, p.full_name AS author "
        "FROM contact_messages m JOIN profiles p ON p.id = m.author_id WHERE m.ticket_id = $1 ORDER BY m.created_at, m.id",
        ticket_id,
    )
    return dict(t) | {"protocol": protocol(t["id"], t["created_at"]), "messages": [dict(m) for m in msgs]}


@router.post("/contact/tickets", status_code=201)
async def open_ticket(body: TicketIn, user=Depends(anyone)):
    async with get_pool().acquire() as conn, conn.transaction():
        tid = await conn.fetchval(
            "INSERT INTO contact_tickets (user_id, topic, reference) VALUES ($1,$2,$3) RETURNING id",
            user["id"], body.topic, body.reference,
        )
        await conn.execute(
            "INSERT INTO contact_messages (ticket_id, author_id, body) VALUES ($1,$2,$3)", tid, user["id"], body.message
        )
        return await ticket_view(conn, tid)


@router.get("/contact/tickets/me")
async def my_tickets(user=Depends(anyone)):
    async with get_pool().acquire() as conn:
        ids = await conn.fetch("SELECT id FROM contact_tickets WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 50", user["id"])
        return [await ticket_view(conn, r["id"]) for r in ids]


@router.post("/contact/tickets/{ticket_id}/messages", status_code=201)
async def add_message(ticket_id: int, body: MessageIn, user=Depends(anyone)):
    async with get_pool().acquire() as conn, conn.transaction():
        owner = await conn.fetchrow("SELECT user_id, status FROM contact_tickets WHERE id = $1 FOR UPDATE", ticket_id)
        if not owner or owner["user_id"] != user["id"]:
            raise HTTPException(404, "Atendimento não encontrado")
        if owner["status"] == "closed":
            raise HTTPException(409, "Este atendimento foi encerrado; abra um novo")
        await conn.execute("INSERT INTO contact_messages (ticket_id, author_id, body) VALUES ($1,$2,$3)", ticket_id, user["id"], body.body)
        await conn.execute("UPDATE contact_tickets SET status = 'open', updated_at = NOW() WHERE id = $1", ticket_id)
        return await ticket_view(conn, ticket_id)


@router.get("/staff/contact/tickets")
async def staff_tickets(status: Optional[Literal["open", "answered", "closed"]] = None, limit: int = Query(100, ge=1, le=300), user=Depends(staff)):
    async with get_pool().acquire() as conn:
        if status:
            ids = await conn.fetch("SELECT id FROM contact_tickets WHERE status = $1 ORDER BY updated_at DESC LIMIT $2", status, limit)
        else:
            ids = await conn.fetch("SELECT id FROM contact_tickets ORDER BY updated_at DESC LIMIT $1", limit)
        return [await ticket_view(conn, r["id"]) for r in ids]


@router.post("/staff/contact/tickets/{ticket_id}/reply")
async def reply_ticket(ticket_id: int, body: ReplyIn, user=Depends(staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        t = await conn.fetchrow("SELECT id, user_id, created_at FROM contact_tickets WHERE id = $1 FOR UPDATE", ticket_id)
        if not t:
            raise HTTPException(404, "Atendimento não encontrado")
        await conn.execute(
            "INSERT INTO contact_messages (ticket_id, author_id, from_staff, body) VALUES ($1,$2,TRUE,$3)", ticket_id, user["id"], body.body
        )
        await conn.execute(
            "UPDATE contact_tickets SET status = $2, updated_at = NOW() WHERE id = $1", ticket_id, "closed" if body.close else "answered"
        )
        await notify(conn, t["user_id"], f"A Videoteca respondeu seu atendimento {protocol(t['id'], t['created_at'])}", body.body[:300], "requests")
        return await ticket_view(conn, ticket_id)


# ── Pedidos de sala do próprio usuário (aluno ou professor) ──────────

@router.get("/rooms/requests/me")
async def my_room_requests(user=Depends(member)):
    rows = await get_pool().fetch(
        "SELECT id, request_type, payload, status, staff_notes, created_at FROM professor_requests "
        "WHERE professor_id = $1 AND request_type = 'room' ORDER BY created_at DESC",
        user["id"],
    )
    return [dict(r) | {"payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]} for r in rows]
