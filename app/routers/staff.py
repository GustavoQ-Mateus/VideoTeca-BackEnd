import json
import secrets
import string
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import hash_password, require_role
from app.db.postgres import get_pool
from app.notify import get_settings, notify

router = APIRouter()
_staff = require_role("funcionario", "admin")

Limit = Query(50, ge=1, le=200)
Offset = Query(0, ge=0)


# ── Fila de reservas ─────────────────────────────────────────────────────────
# Recebem conexão (em transação). expire_reservations roda nas listagens/estatísticas;
# promote_waiting roda na devolução, no cancelamento/expiração de reserva 'ready' e quando um
# exemplar fica disponível.

LOCAL_TZ = timezone(timedelta(hours=-3))  # America/Fortaleza (sem horário de verão)


def fmt_local(value: datetime | None) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d/%m/%Y %H:%M") if value else ""


async def free_copies(conn, film_id: int) -> int:
    """Exemplares disponíveis sem empréstimo aberto, menos os já separados para reservas 'ready'."""
    return await conn.fetchval(
        "SELECT (SELECT COUNT(*) FROM copies c WHERE c.film_id = $1 AND c.status = 'available' "
        "        AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.returned_at IS NULL)) "
        "     - (SELECT COUNT(*) FROM reservations r WHERE r.film_id = $1 AND r.status = 'ready')",
        film_id,
    )


async def mark_ready(conn, reservation_id: int, pickup_days: int):
    """waiting -> ready com prazo de retirada; avisa o aluno. Retorna a linha ou None."""
    row = await conn.fetchrow(
        "UPDATE reservations r SET status = 'ready', pickup_until = NOW() + make_interval(days => COALESCE(f.pickup_days, $2)) "
        "FROM films f WHERE r.id = $1 AND r.status = 'waiting' AND f.id = r.film_id "
        "RETURNING r.id, r.user_id, r.film_id, r.status, r.pickup_until, f.title",
        reservation_id,
        pickup_days,
    )
    if row:
        await notify(
            conn,
            row["user_id"],
            "Reserva pronta para retirada",
            f'Sua reserva de "{row["title"]}" está disponível na Videoteca até {fmt_local(row["pickup_until"])}.',
            "reservations",
        )
    return row


async def promote_waiting(conn, film_id: int) -> list[int]:
    """Promove a fila do filme enquanto houver exemplar livre. Retorna os ids promovidos."""
    # Serializa a fila por filme (devoluções, cancelamentos e confirmações simultâneas).
    await conn.execute("SELECT id FROM films WHERE id = $1 FOR UPDATE", film_id)
    free = await free_copies(conn, film_id)
    if free <= 0:
        return []
    settings = await get_settings(conn)
    waiting = await conn.fetch(
        "SELECT id FROM reservations WHERE film_id = $1 AND status = 'waiting' "
        "ORDER BY requested_at ASC, id ASC LIMIT $2 FOR UPDATE",
        film_id,
        free,
    )
    promoted = []
    for r in waiting:
        if await mark_ready(conn, r["id"], settings["reservation_pickup_days"]):
            promoted.append(r["id"])
    return promoted


async def expire_reservations(conn) -> int:
    """Reservas 'ready' não retiradas no prazo viram 'expired'; o exemplar passa ao próximo da fila."""
    async with conn.transaction():
        rows = await conn.fetch(
            "UPDATE reservations r SET status = 'expired' FROM films f "
            "WHERE r.status = 'ready' AND r.pickup_until < NOW() AND f.id = r.film_id "
            "RETURNING r.id, r.user_id, r.film_id, f.title"
        )
        for r in rows:
            await notify(
                conn,
                r["user_id"],
                "Reserva expirada",
                f'O prazo para retirar "{r["title"]}" terminou e a reserva foi encerrada.',
                "reservations",
            )
        for film_id in sorted({r["film_id"] for r in rows}):
            await promote_waiting(conn, film_id)
    return len(rows)


def _like(q: str) -> str:
    """Padrão ILIKE com curingas do usuário escapados."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ── Dashboard stats ──────────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats(user=Depends(_staff)):
    async with get_pool().acquire() as conn:
        await expire_reservations(conn)
        row = await conn.fetchrow(
            "SELECT "
            "(SELECT COUNT(*) FROM films WHERE status <> 'inactive') AS total_films, "
            "(SELECT COUNT(*) FROM loans WHERE returned_at IS NULL) AS active_loans, "
            "(SELECT COUNT(*) FROM reservations WHERE status = 'waiting') AS pending_reservations, "
            "(SELECT COUNT(*) FROM reservations WHERE status = 'ready') AS ready_reservations, "
            "(SELECT COUNT(*) FROM loans WHERE returned_at IS NULL AND due_at < NOW()) AS late_items, "
            "(SELECT COUNT(*) FROM professor_requests WHERE status = 'analysis') AS pending_requests, "
            "(SELECT COUNT(*) FROM events WHERE starts_at > NOW() AND status = 'published') AS upcoming_events"
        )
    return dict(row)


# ── Inventory (leitura; o cadastro de filmes fica em /staff/films) ─────────────


@router.get("/inventory")
async def list_inventory(
    limit: int = Query(1000, ge=1, le=1000), offset: int = Offset, user=Depends(_staff)
):
    rows = await get_pool().fetch(
        "SELECT f.*, "
        "(SELECT COUNT(*) FROM copies c WHERE c.film_id = f.id AND c.status = 'available' "
        " AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.returned_at IS NULL)) AS available_copies, "
        "(SELECT COUNT(*) FROM copies c WHERE c.film_id = f.id) AS total_copies "
        "FROM films f WHERE f.status <> 'inactive' ORDER BY f.title ASC, f.id ASC LIMIT $1 OFFSET $2",
        limit,
        offset,
    )
    return [dict(r) for r in rows]


# ── Parâmetros do sistema ─────────────────────────────────────────────────────


class SettingsInput(BaseModel):
    loan_days: int = Field(ge=1, le=60)
    max_items_per_user: int = Field(ge=1, le=20)
    max_reservations: int = Field(ge=0, le=20)
    reservation_pickup_days: int = Field(ge=1, le=15)


@router.get("/settings")
async def read_settings(user=Depends(_staff)):
    async with get_pool().acquire() as conn:
        return await get_settings(conn)


@router.put("/settings")
async def update_settings(body: SettingsInput, user=Depends(_staff)):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (id, loan_days, max_items_per_user, max_reservations, reservation_pickup_days, updated_at) "
            "VALUES (1, $1, $2, $3, $4, NOW()) ON CONFLICT (id) DO UPDATE SET loan_days = EXCLUDED.loan_days, "
            "max_items_per_user = EXCLUDED.max_items_per_user, max_reservations = EXCLUDED.max_reservations, "
            "reservation_pickup_days = EXCLUDED.reservation_pickup_days, updated_at = NOW()",
            body.loan_days,
            body.max_items_per_user,
            body.max_reservations,
            body.reservation_pickup_days,
        )
        return await get_settings(conn)


# ── Loans ────────────────────────────────────────────────────────────────────

LOAN_SELECT = (
    "SELECT l.id, "
    "CASE WHEN l.returned_at IS NOT NULL THEN 'returned' WHEN l.due_at < NOW() THEN 'late' ELSE 'borrowed' END AS status, "
    "l.borrowed_at, l.due_at, l.returned_at, l.return_condition, l.return_notes, "
    "f.id AS film_id, f.title AS film, f.cover, c.id AS copy_id, c.code AS copy, "
    "p.id AS user_id, p.full_name AS user_name, p.registration, p.course "
    "FROM loans l "
    "JOIN copies c ON c.id = l.copy_id "
    "JOIN films f ON f.id = c.film_id "
    "JOIN profiles p ON p.id = l.user_id "
)


def _loan_out(row) -> dict:
    return dict(row) | {"user_id": str(row["user_id"])}


@router.get("/loans")
async def list_loans(
    open: Optional[bool] = None,
    q: Optional[str] = Query(None, max_length=120),
    limit: int = Limit,
    offset: int = Offset,
    user=Depends(_staff),
):
    """Empréstimos (mais recentes primeiro). open=true: só os não devolvidos; q busca código do
    exemplar, matrícula, nome ou título. status é derivado: borrowed | late | returned."""
    where, args = [], []
    if open is not None:
        where.append("l.returned_at IS NULL" if open else "l.returned_at IS NOT NULL")
    if q and q.strip():
        args.append(_like(q.strip()))
        n = len(args)
        where.append(
            f"(c.code ILIKE ${n} OR p.registration ILIKE ${n} OR f.title ILIKE ${n} OR p.full_name ILIKE ${n})"
        )
    args += [limit, offset]
    sql = (
        LOAN_SELECT
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + f"ORDER BY l.borrowed_at DESC, l.id DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}"
    )
    return [_loan_out(r) for r in await get_pool().fetch(sql, *args)]


async def _loan_row(conn, loan_id: int) -> dict:
    return _loan_out(await conn.fetchrow(LOAN_SELECT + "WHERE l.id = $1", loan_id))


async def _borrower(conn, registration: str):
    profile = await conn.fetchrow(
        "SELECT id, full_name, active FROM profiles WHERE lower(registration) = lower($1) FOR UPDATE",
        registration.strip(),
    )
    if not profile:
        raise HTTPException(404, "Usuário não encontrado")
    if not profile["active"]:
        raise HTTPException(409, "Conta desativada")
    return profile


async def _lendable_copy(conn, copy_id: int):
    copy = await conn.fetchrow(
        "SELECT c.id, c.film_id, c.code, c.status, "
        "EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.returned_at IS NULL) AS on_loan "
        "FROM copies c WHERE c.id = $1 FOR UPDATE",
        copy_id,
    )
    if copy["status"] != "available" or copy["on_loan"]:
        raise HTTPException(409, "Exemplar não disponível")
    return copy


async def _lend(conn, user_id, copy, days: int | None = None) -> dict:
    """Cria o empréstimo (já com perfil, filme e exemplar travados) e dá baixa na reserva do usuário.

    Prazo: `days` informado no balcão > regra do filme > regra padrão (app_settings)."""
    settings = await get_settings(conn)
    film = await conn.fetchrow("SELECT loan_days, local_only FROM films WHERE id = $1", copy["film_id"])
    if film and film["local_only"]:
        raise HTTPException(409, "Este filme é só para assistir na Videoteca e não sai em empréstimo")
    loan_days = days or (film["loan_days"] if film else None) or settings["loan_days"]
    open_loans = await conn.fetchval(
        "SELECT COUNT(*) FROM loans WHERE user_id = $1 AND returned_at IS NULL", user_id
    )
    if open_loans >= settings["max_items_per_user"]:
        raise HTTPException(
            409, f"Limite de {settings['max_items_per_user']} itens emprestados por usuário atingido"
        )
    loan_id = await conn.fetchval(
        "INSERT INTO loans (user_id, copy_id, due_at) VALUES ($1, $2, NOW() + make_interval(days => $3)) RETURNING id",
        user_id,
        copy["id"],
        loan_days,
    )
    await conn.execute("UPDATE copies SET status = 'borrowed' WHERE id = $1", copy["id"])
    fulfilled = await conn.fetchval(
        "UPDATE reservations SET status = 'fulfilled' "
        "WHERE user_id = $1 AND film_id = $2 AND status IN ('waiting', 'ready') RETURNING id",
        user_id,
        copy["film_id"],
    )
    return await _loan_row(conn, loan_id) | {"reservation_fulfilled": fulfilled}


class LoanCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    user_registration: str = Field(min_length=1, max_length=40)
    copy_code: str = Field(min_length=1, max_length=60)
    # Ajuste do prazo só para este empréstimo (sem mudar a regra do filme).
    days: Optional[int] = Field(default=None, ge=1, le=60)


@router.post("/loans", status_code=201)
async def create_loan(body: LoanCreate, user=Depends(_staff)):
    """Prazo = loan_days; recusa acima de max_items_per_user e exemplar separado para outra reserva."""
    async with get_pool().acquire() as conn, conn.transaction():
        profile = await _borrower(conn, body.user_registration)
        film_id = await conn.fetchval("SELECT film_id FROM copies WHERE code = $1", body.copy_code)
        if film_id is None:
            raise HTTPException(404, "Exemplar não encontrado")
        await conn.execute("SELECT id FROM films WHERE id = $1 FOR UPDATE", film_id)
        copy_id = await conn.fetchval("SELECT id FROM copies WHERE code = $1", body.copy_code)
        copy = await _lendable_copy(conn, copy_id)
        has_ready = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM reservations WHERE user_id = $1 AND film_id = $2 AND status = 'ready')",
            profile["id"],
            film_id,
        )
        if not has_ready and await free_copies(conn, film_id) <= 0:
            raise HTTPException(409, "Os exemplares livres deste filme estão separados para reservas de outros usuários")
        return await _lend(conn, profile["id"], copy, body.days)


class ReturnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    condition: Literal["good", "damaged"] = "good"
    notes: Optional[str] = Field(default=None, max_length=2000)


@router.post("/loans/{loan_id}/return")
async def return_loan(loan_id: int, body: Optional[ReturnInput] = None, user=Depends(_staff)):
    """Registra a devolução; dano manda o exemplar para manutenção, senão ele volta e promove a fila."""
    body = body or ReturnInput()
    async with get_pool().acquire() as conn, conn.transaction():
        loan = await conn.fetchrow(
            "SELECT l.id, l.copy_id, c.film_id FROM loans l JOIN copies c ON c.id = l.copy_id "
            "WHERE l.id = $1 AND l.returned_at IS NULL FOR UPDATE OF l",
            loan_id,
        )
        if not loan:
            raise HTTPException(404, "Empréstimo não encontrado ou já devolvido")
        await conn.execute("SELECT id FROM films WHERE id = $1 FOR UPDATE", loan["film_id"])
        await conn.execute(
            "UPDATE loans SET status = 'returned', returned_at = NOW(), return_condition = $2, return_notes = $3 WHERE id = $1",
            loan_id,
            body.condition,
            body.notes or None,
        )
        damaged = body.condition == "damaged"
        copy_status = "maintenance" if damaged else "available"
        await conn.execute(
            "UPDATE copies SET status = $2, condition = CASE WHEN $3 THEN 'damaged' ELSE condition END WHERE id = $1",
            loan["copy_id"],
            copy_status,
            damaged,
        )
        promoted = [] if damaged else await promote_waiting(conn, loan["film_id"])
    return {
        "ok": True,
        "id": loan_id,
        "condition": body.condition,
        "copy_status": copy_status,
        "promoted_reservations": promoted,
    }


# ── Copies ───────────────────────────────────────────────────────────────────

CopyCondition = Literal["excellent", "good", "fair", "damaged"]


def _copy_out(row) -> dict:
    out = {k: row[k] for k in ("id", "code", "patrimonio", "condition", "status")}
    out["open_loan"] = (
        {"user_name": row["loan_user"], "due_at": row["loan_due"]} if row["loan_due"] is not None else None
    )
    return out


COPY_SELECT = (
    "SELECT c.id, c.code, c.patrimonio, c.condition, c.status, ol.user_name AS loan_user, ol.due_at AS loan_due "
    "FROM copies c LEFT JOIN LATERAL (SELECT p.full_name AS user_name, l.due_at FROM loans l "
    "JOIN profiles p ON p.id = l.user_id WHERE l.copy_id = c.id AND l.returned_at IS NULL LIMIT 1) ol ON TRUE "
)


@router.get("/films/{film_id}/copies")
async def list_copies(film_id: int, user=Depends(_staff)):
    pool = get_pool()
    if not await pool.fetchval("SELECT 1 FROM films WHERE id = $1 AND status <> 'inactive'", film_id):
        raise HTTPException(404, "Filme não encontrado")
    rows = await pool.fetch(COPY_SELECT + "WHERE c.film_id = $1 ORDER BY c.code", film_id)
    return [_copy_out(r) for r in rows]


class CopyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    code: Optional[str] = Field(default=None, max_length=60)
    patrimonio: Optional[str] = Field(default=None, max_length=60)
    condition: CopyCondition = "good"


@router.post("/films/{film_id}/copies", status_code=201)
async def create_copy(film_id: int, body: CopyCreate, user=Depends(_staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        film = await conn.fetchrow(
            "SELECT id, internal_code FROM films WHERE id = $1 AND status <> 'inactive' FOR UPDATE", film_id
        )
        if not film:
            raise HTTPException(404, "Filme não encontrado")
        code = body.code
        if code:
            if await conn.fetchval("SELECT 1 FROM copies WHERE code = $1", code):
                raise HTTPException(409, "Já existe um exemplar com este código")
        else:
            base = film["internal_code"] or f"FILME{film_id}"
            n = await conn.fetchval("SELECT COUNT(*) FROM copies WHERE film_id = $1", film_id) + 1
            while await conn.fetchval("SELECT 1 FROM copies WHERE code = $1", f"{base}-{n}"):
                n += 1
            code = f"{base}-{n}"
        copy_id = await conn.fetchval(
            "INSERT INTO copies (film_id, code, patrimonio, condition, status) VALUES ($1, $2, $3, $4, 'available') RETURNING id",
            film_id,
            code,
            body.patrimonio or None,
            body.condition,
        )
        await promote_waiting(conn, film_id)
        return _copy_out(await conn.fetchrow(COPY_SELECT + "WHERE c.id = $1", copy_id))


class CopyUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    code: Optional[str] = Field(default=None, min_length=1, max_length=60)
    patrimonio: Optional[str] = Field(default=None, max_length=60)
    condition: Optional[CopyCondition] = None
    status: Optional[Literal["available", "maintenance", "lost"]] = None


@router.put("/copies/{copy_id}")
async def update_copy(copy_id: int, body: CopyUpdate, user=Depends(_staff)):
    changes = body.model_dump(exclude_unset=True)
    async with get_pool().acquire() as conn, conn.transaction():
        film_id = await conn.fetchval("SELECT film_id FROM copies WHERE id = $1", copy_id)
        if film_id is None:
            raise HTTPException(404, "Exemplar não encontrado")
        await conn.execute("SELECT id FROM films WHERE id = $1 FOR UPDATE", film_id)
        copy = await conn.fetchrow(
            "SELECT c.*, EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.returned_at IS NULL) AS on_loan "
            "FROM copies c WHERE c.id = $1 FOR UPDATE",
            copy_id,
        )
        if changes.get("status") and copy["on_loan"]:
            raise HTTPException(409, "Exemplar emprestado: registre a devolução antes de mudar o status")
        if changes.get("code") and changes["code"] != copy["code"]:
            if await conn.fetchval("SELECT 1 FROM copies WHERE code = $1 AND id <> $2", changes["code"], copy_id):
                raise HTTPException(409, "Já existe um exemplar com este código")
        if "code" in changes and not changes["code"]:
            changes.pop("code")
        if changes:
            cols = list(changes)
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await conn.execute(f"UPDATE copies SET {sets} WHERE id = $1", copy_id, *[changes[c] for c in cols])
        if changes.get("status") == "available":
            await promote_waiting(conn, film_id)
        return _copy_out(await conn.fetchrow(COPY_SELECT + "WHERE c.id = $1", copy_id))


@router.delete("/copies/{copy_id}")
async def delete_copy(copy_id: int, user=Depends(_staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        copy = await conn.fetchrow("SELECT id FROM copies WHERE id = $1 FOR UPDATE", copy_id)
        if not copy:
            raise HTTPException(404, "Exemplar não encontrado")
        if await conn.fetchval("SELECT 1 FROM loans WHERE copy_id = $1 AND returned_at IS NULL", copy_id):
            raise HTTPException(409, "Exemplar com empréstimo em aberto não pode ser excluído")
        if await conn.fetchval("SELECT 1 FROM loans WHERE copy_id = $1 LIMIT 1", copy_id):
            raise HTTPException(
                409, "Exemplar com histórico de empréstimos: marque-o como extraviado ou em manutenção"
            )
        await conn.execute("DELETE FROM copies WHERE id = $1", copy_id)
    return {"ok": True}


# ── Reservations ─────────────────────────────────────────────────────────────


RES_SELECT = (
    "SELECT r.id, r.status, r.requested_at, r.pickup_until, r.film_id, "
    "f.title AS film, f.media, f.cover, r.user_id, p.full_name AS user_name, p.registration, "
    "CASE WHEN r.status = 'waiting' THEN (SELECT COUNT(*) FROM reservations w WHERE w.film_id = r.film_id "
    " AND w.status = 'waiting' AND (w.requested_at, w.id) <= (r.requested_at, r.id)) END AS queue_position "
    "FROM reservations r JOIN films f ON f.id = r.film_id JOIN profiles p ON p.id = r.user_id "
)


def _res_out(row) -> dict:
    return dict(row) | {"user_id": str(row["user_id"])}


@router.get("/reservations")
async def list_reservations(
    status: Optional[Literal["waiting", "ready", "active", "fulfilled", "cancelled", "expired"]] = None,
    limit: int = Limit,
    offset: int = Offset,
    user=Depends(_staff),
):
    """Sem status: todas menos as canceladas. active = waiting + ready."""
    async with get_pool().acquire() as conn:
        await expire_reservations(conn)
        if status is None:
            where, args = "r.status <> 'cancelled'", []
        elif status == "active":
            where, args = "r.status IN ('waiting', 'ready')", []
        else:
            where, args = "r.status = $3", [status]
        rows = await conn.fetch(
            RES_SELECT + f"WHERE {where} ORDER BY r.requested_at DESC, r.id DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
            *args,
        )
    return [_res_out(r) for r in rows]


class StaffReservationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    registration: str = Field(min_length=1, max_length=40)
    film_id: int = Field(gt=0)


@router.post("/reservations", status_code=201)
async def create_reservation(body: StaffReservationCreate, user=Depends(_staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        profile = await _borrower(conn, body.registration)
        film = await conn.fetchrow("SELECT id, status FROM films WHERE id = $1 FOR UPDATE", body.film_id)
        if not film or film["status"] == "inactive":
            raise HTTPException(404, "Filme não encontrado")
        if film["status"] == "maintenance":
            raise HTTPException(409, "Filme em manutenção não pode ser reservado")
        active = await conn.fetch(
            "SELECT film_id FROM reservations WHERE user_id = $1 AND status IN ('waiting', 'ready')", profile["id"]
        )
        if any(r["film_id"] == body.film_id for r in active):
            raise HTTPException(409, "Usuário já tem reserva ativa para este filme")
        if await conn.fetchval(
            "SELECT 1 FROM loans l JOIN copies c ON c.id = l.copy_id WHERE l.user_id = $1 AND c.film_id = $2 AND l.returned_at IS NULL",
            profile["id"],
            body.film_id,
        ):
            raise HTTPException(409, "Usuário já está com este filme emprestado")
        settings = await get_settings(conn)
        if len(active) >= settings["max_reservations"]:
            raise HTTPException(409, f"Limite de {settings['max_reservations']} reservas ativas atingido")
        rid = await conn.fetchval(
            "INSERT INTO reservations (user_id, film_id, status) VALUES ($1, $2, 'waiting') RETURNING id",
            profile["id"],
            body.film_id,
        )
        return _res_out(await conn.fetchrow(RES_SELECT + "WHERE r.id = $1", rid))


async def _locked_reservation(conn, reservation_id: int):
    res = await conn.fetchrow("SELECT * FROM reservations WHERE id = $1", reservation_id)
    if not res:
        raise HTTPException(404, "Reserva não encontrada")
    await conn.execute("SELECT id FROM films WHERE id = $1 FOR UPDATE", res["film_id"])
    return await conn.fetchrow("SELECT * FROM reservations WHERE id = $1 FOR UPDATE", reservation_id)


@router.put("/reservations/{reservation_id}/confirm")
async def confirm_reservation(reservation_id: int, user=Depends(_staff)):
    """waiting -> ready (exemplar separado; prazo de retirada = reservation_pickup_days) e avisa o aluno."""
    async with get_pool().acquire() as conn, conn.transaction():
        await expire_reservations(conn)
        res = await _locked_reservation(conn, reservation_id)
        if res["status"] != "waiting":
            raise HTTPException(409, "Só reservas na fila podem ser confirmadas")
        if await free_copies(conn, res["film_id"]) <= 0:
            raise HTTPException(409, "Nenhum exemplar livre para esta reserva")
        settings = await get_settings(conn)
        row = await mark_ready(conn, reservation_id, settings["reservation_pickup_days"])
    return {"ok": True, "id": row["id"], "status": row["status"], "pickup_until": row["pickup_until"]}


@router.delete("/reservations/{reservation_id}")
async def cancel_reservation(reservation_id: int, user=Depends(_staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        res = await _locked_reservation(conn, reservation_id)
        if res["status"] not in ("waiting", "ready"):
            raise HTTPException(409, "Reserva já encerrada")
        await conn.execute("UPDATE reservations SET status = 'cancelled' WHERE id = $1", reservation_id)
        title = await conn.fetchval("SELECT title FROM films WHERE id = $1", res["film_id"])
        await notify(
            conn, res["user_id"], "Reserva cancelada",
            f'Sua reserva de "{title}" foi cancelada pela Videoteca.', "reservations",
        )
        promoted = await promote_waiting(conn, res["film_id"]) if res["status"] == "ready" else []
    return {"ok": True, "promoted_reservations": promoted}


class CheckoutInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    copy_code: Optional[str] = Field(default=None, max_length=60)


@router.post("/reservations/{reservation_id}/checkout", status_code=201)
async def checkout_reservation(
    reservation_id: int, body: Optional[CheckoutInput] = None, user=Depends(_staff)
):
    """Converte a reserva em empréstimo (exemplar informado ou o primeiro livre) e a marca fulfilled."""
    body = body or CheckoutInput()
    async with get_pool().acquire() as conn, conn.transaction():
        await expire_reservations(conn)
        res = await conn.fetchrow("SELECT * FROM reservations WHERE id = $1", reservation_id)
        if not res:
            raise HTTPException(404, "Reserva não encontrada")
        profile = await conn.fetchrow("SELECT id, active FROM profiles WHERE id = $1 FOR UPDATE", res["user_id"])
        if not profile["active"]:
            raise HTTPException(409, "Conta desativada")
        res = await _locked_reservation(conn, reservation_id)
        if res["status"] not in ("waiting", "ready"):
            raise HTTPException(409, "Reserva já encerrada")
        if body.copy_code:
            copy_id = await conn.fetchval(
                "SELECT id FROM copies WHERE code = $1 AND film_id = $2", body.copy_code, res["film_id"]
            )
            if copy_id is None:
                raise HTTPException(404, "Exemplar não encontrado para este filme")
        else:
            copy_id = await conn.fetchval(
                "SELECT c.id FROM copies c WHERE c.film_id = $1 AND c.status = 'available' "
                "AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.returned_at IS NULL) "
                "ORDER BY c.code LIMIT 1",
                res["film_id"],
            )
            if copy_id is None:
                raise HTTPException(409, "Nenhum exemplar disponível para este filme")
        copy = await _lendable_copy(conn, copy_id)
        if res["status"] == "waiting" and await free_copies(conn, res["film_id"]) <= 0:
            raise HTTPException(409, "Os exemplares livres estão separados para reservas anteriores")
        return await _lend(conn, res["user_id"], copy)


# ── Users ────────────────────────────────────────────────────────────────────

USER_SELECT = (
    "SELECT p.id, p.full_name, p.role, p.registration, p.course, p.phone, p.active, u.email, p.created_at, "
    "(SELECT COUNT(*) FROM loans l WHERE l.user_id = p.id AND l.returned_at IS NULL) AS active_loans "
    "FROM profiles p JOIN users u ON u.id = p.id "
)
Role = Literal["aluno", "professor", "funcionario", "admin"]
_TEMP_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def _user_out(row) -> dict:
    return dict(row) | {"id": str(row["id"])}


def _temporary_password() -> str:
    while True:
        pw = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _check_can_manage(actor, target_role: str):
    if target_role in ("funcionario", "admin") and actor["role"] != "admin":
        raise HTTPException(403, "Apenas administradores gerenciam contas de equipe")


@router.get("/users")
async def list_users(
    q: Optional[str] = Query(None, max_length=120),
    role: Optional[Role] = None,
    active: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Offset,
    user=Depends(_staff),
):
    where, args = ["u.email NOT LIKE '%@anon.invalid'"], []
    if q and q.strip():
        args.append(_like(q.strip()))
        n = len(args)
        where.append(f"(p.full_name ILIKE ${n} OR u.email ILIKE ${n} OR p.registration ILIKE ${n})")
    if role:
        args.append(role)
        where.append(f"p.role = ${len(args)}")
    if active is not None:
        args.append(active)
        where.append(f"p.active = ${len(args)}")
    args += [limit, offset]
    rows = await get_pool().fetch(
        USER_SELECT + "WHERE " + " AND ".join(where)
        + f" ORDER BY p.full_name ASC, p.id LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return [_user_out(r) for r in rows]


@router.get("/users/by-registration/{registration}")
async def get_user_by_registration(registration: str, user=Depends(_staff)):
    row = await get_pool().fetchrow(USER_SELECT + "WHERE lower(p.registration) = lower($1)", registration)
    if not row:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return _user_out(row)


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role
    registration: Optional[str] = Field(default=None, max_length=40)
    course: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)


async def _registration_taken(conn, registration, exclude=None) -> bool:
    return bool(
        registration
        and await conn.fetchval(
            "SELECT 1 FROM profiles WHERE lower(registration) = lower($1) AND ($2::uuid IS NULL OR id <> $2)",
            registration,
            exclude,
        )
    )


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, user=Depends(_staff)):
    """Cria a conta com senha temporária aleatória, devolvida só nesta resposta."""
    _check_can_manage(user, body.role)
    email = body.email.lower()
    password = _temporary_password()
    async with get_pool().acquire() as conn, conn.transaction():
        if await conn.fetchval("SELECT 1 FROM users WHERE lower(email) = $1", email):
            raise HTTPException(409, "E-mail já cadastrado")
        if await _registration_taken(conn, body.registration):
            raise HTTPException(409, "Matrícula já cadastrada")
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id", email, hash_password(password)
        )
        await conn.execute(
            "INSERT INTO profiles (id, full_name, role, registration, course, phone, active) VALUES ($1,$2,$3,$4,$5,$6,TRUE)",
            uid, body.full_name, body.role, body.registration or None, body.course or None, body.phone or None,
        )
        row = await conn.fetchrow(USER_SELECT + "WHERE p.id = $1", uid)
    return {"user": _user_out(row), "temporary_password": password}


async def _target(conn, actor, user_id: UUID):
    row = await conn.fetchrow(
        "SELECT p.id, p.role, u.email FROM profiles p JOIN users u ON u.id = p.id WHERE p.id = $1 FOR UPDATE OF p",
        user_id,
    )
    if not row or row["email"].endswith("@anon.invalid"):
        raise HTTPException(404, "Usuário não encontrado")
    _check_can_manage(actor, row["role"])
    return row


class UserUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    course: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    registration: Optional[str] = Field(default=None, max_length=40)


@router.put("/users/{user_id}")
async def update_user(user_id: UUID, body: UserUpdate, user=Depends(_staff)):
    changes = body.model_dump(exclude_unset=True)
    if changes.get("full_name", "") is None:
        raise HTTPException(422, "Informe o nome")
    async with get_pool().acquire() as conn, conn.transaction():
        await _target(conn, user, user_id)
        if await _registration_taken(conn, changes.get("registration"), user_id):
            raise HTTPException(409, "Matrícula já cadastrada")
        if changes:
            cols = list(changes)
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            values = [changes[c] if c == "full_name" else (changes[c] or None) for c in cols]
            await conn.execute(f"UPDATE profiles SET {sets} WHERE id = $1", user_id, *values)
        return _user_out(await conn.fetchrow(USER_SELECT + "WHERE p.id = $1", user_id))


class StatusInput(BaseModel):
    active: bool


@router.patch("/users/{user_id}/status")
async def set_user_status(user_id: UUID, body: StatusInput, user=Depends(_staff)):
    if str(user_id) == str(user["id"]) and not body.active:
        raise HTTPException(409, "Você não pode desativar a própria conta")
    async with get_pool().acquire() as conn, conn.transaction():
        await _target(conn, user, user_id)
        await conn.execute("UPDATE profiles SET active = $2 WHERE id = $1", user_id, body.active)
        return _user_out(await conn.fetchrow(USER_SELECT + "WHERE p.id = $1", user_id))


# ── Rooms ────────────────────────────────────────────────────────────────────

ROOM_SELECT = (
    "SELECT r.*, EXISTS (SELECT 1 FROM room_bookings b WHERE b.room_id = r.id AND b.status = 'confirmed' "
    "AND b.starts_at <= NOW() AND b.ends_at > NOW()) AS occupied_now FROM rooms r "
)
RoomStatus = Literal["available", "inactive", "maintenance"]


@router.get("/rooms")
async def list_rooms(user=Depends(_staff)):
    rows = await get_pool().fetch(ROOM_SELECT + "ORDER BY r.name ASC")
    return [dict(r) for r in rows]


class RoomCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    capacity: int = Field(ge=1, le=10000)
    equipment: list[str] = Field(default_factory=list, max_length=30)
    status: RoomStatus = "available"


class RoomUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    capacity: Optional[int] = Field(default=None, ge=1, le=10000)
    equipment: Optional[list[str]] = Field(default=None, max_length=30)
    status: Optional[RoomStatus] = None


def _clean_equipment(items):
    out = [i.strip()[:60] for i in items if i and i.strip()]
    return list(dict.fromkeys(out))


@router.post("/rooms", status_code=201)
async def create_room(body: RoomCreate, user=Depends(_staff)):
    pool = get_pool()
    rid = await pool.fetchval(
        "INSERT INTO rooms (name, capacity, equipment, status) VALUES ($1, $2, $3, $4) RETURNING id",
        body.name, body.capacity, _clean_equipment(body.equipment), body.status,
    )
    return dict(await pool.fetchrow(ROOM_SELECT + "WHERE r.id = $1", rid))


@router.put("/rooms/{room_id}")
async def update_room(room_id: int, body: RoomUpdate, user=Depends(_staff)):
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "equipment" in changes:
        changes["equipment"] = _clean_equipment(changes["equipment"])
    async with get_pool().acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT id FROM rooms WHERE id = $1 FOR UPDATE", room_id):
            raise HTTPException(404, "Sala não encontrada")
        sessions = await conn.fetchrow(
            "SELECT COUNT(*) AS n, MAX(slots) AS max_slots FROM events "
            "WHERE room_id = $1 AND status = 'published' AND COALESCE(ends_at, starts_at) > NOW()",
            room_id,
        )
        if sessions["n"] and changes.get("status", "available") != "available":
            raise HTTPException(409, "A sala tem sessões publicadas futuras; despublique-as antes")
        if sessions["n"] and changes.get("capacity", 10**9) < sessions["max_slots"]:
            raise HTTPException(409, "Capacidade menor que as vagas de uma sessão publicada nesta sala")
        if changes:
            cols = list(changes)
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await conn.execute(f"UPDATE rooms SET {sets} WHERE id = $1", room_id, *[changes[c] for c in cols])
        return dict(await conn.fetchrow(ROOM_SELECT + "WHERE r.id = $1", room_id))


@router.get("/room-bookings")
async def list_room_bookings(
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    user=Depends(_staff),
):
    """Ocupações (reservas de professor e sessões publicadas) no intervalo [from, to)."""
    if to <= from_:
        raise HTTPException(422, "Intervalo inválido")
    rows = await get_pool().fetch(
        "SELECT b.id, b.room_id, r.name AS room_name, b.starts_at, b.ends_at, "
        "COALESCE(b.title, e.title) AS title, b.status, b.event_id, "
        "CASE WHEN b.event_id IS NULL THEN 'booking' ELSE 'session' END AS kind, "
        "p.full_name AS professor_name "
        "FROM room_bookings b JOIN rooms r ON r.id = b.room_id "
        "LEFT JOIN events e ON e.id = b.event_id "
        "LEFT JOIN profiles p ON p.id = b.professor_id "
        "WHERE b.status IN ('confirmed', 'pending') AND b.starts_at < $2 AND b.ends_at > $1 "
        "ORDER BY b.starts_at ASC LIMIT 1000",
        from_,
        to,
    )
    return [dict(r) for r in rows]


# ── Events (leitura; criação e publicação ficam em /staff/sessions) ───────────


@router.get("/events")
async def list_events(limit: int = Query(100, ge=1, le=500), offset: int = Offset, user=Depends(_staff)):
    rows = await get_pool().fetch(
        "SELECT e.*, f.title AS film_title FROM events e "
        "LEFT JOIN films f ON f.id = e.film_id ORDER BY e.starts_at DESC NULLS LAST, e.id DESC LIMIT $1 OFFSET $2",
        limit,
        offset,
    )
    return [dict(r) for r in rows]


@router.get("/events/{event_id}/registrations")
async def list_event_registrations(event_id: int, user=Depends(_staff)):
    pool = get_pool()
    if not await pool.fetchval("SELECT 1 FROM events WHERE id = $1", event_id):
        raise HTTPException(404, "Evento não encontrado")
    rows = await pool.fetch(
        "SELECT er.user_id, p.full_name AS name, p.registration AS matricula, "
        "p.course, COALESCE(er.present, FALSE) AS present "
        "FROM event_registrations er JOIN profiles p ON p.id = er.user_id "
        "WHERE er.event_id = $1 ORDER BY p.full_name ASC",
        event_id,
    )
    return [dict(r) | {"user_id": str(r["user_id"])} for r in rows]


class PresenceUpdate(BaseModel):
    present: bool


@router.put("/events/{event_id}/registrations/{user_id}")
async def update_event_presence(
    event_id: int, user_id: UUID, body: PresenceUpdate, user=Depends(_staff)
):
    row = await get_pool().fetchrow(
        "UPDATE event_registrations SET present = $1 "
        "WHERE event_id = $2 AND user_id = $3 RETURNING user_id, present",
        body.present,
        event_id,
        user_id,
    )
    if not row:
        raise HTTPException(404, "Inscrição não encontrada")
    return {"user_id": str(row["user_id"]), "present": row["present"]}


# ── Professor requests ────────────────────────────────────────────────────────


@router.get("/requests")
async def list_requests(
    status: Optional[Literal["analysis", "approved", "refused"]] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Offset,
    user=Depends(_staff),
):
    rows = await get_pool().fetch(
        "SELECT pr.id, pr.request_type, pr.payload, pr.status, pr.staff_notes, pr.created_at, "
        "p.full_name as professor_name, p.registration "
        "FROM professor_requests pr "
        "JOIN profiles p ON p.id = pr.professor_id "
        "WHERE ($3::text IS NULL OR pr.status = $3) "
        "ORDER BY pr.created_at DESC, pr.id DESC LIMIT $1 OFFSET $2",
        limit,
        offset,
        status,
    )
    return [
        dict(r)
        | {
            "payload": (
                json.loads(r["payload"])
                if isinstance(r["payload"], str)
                else r["payload"]
            )
        }
        for r in rows
    ]


class RequestUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: Literal["analysis", "approved", "refused"]
    staff_notes: Optional[str] = Field(default=None, max_length=2000)


_REQUEST_TITLES = {
    "analysis": "Solicitação em análise",
    "approved": "Solicitação aprovada",
    "refused": "Solicitação recusada",
}
_REQUEST_KINDS = {"film": "filme", "room": "sala"}


@router.put("/requests/{request_id}")
async def update_request(request_id: int, body: RequestUpdate, user=Depends(_staff)):
    async with get_pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM professor_requests WHERE id=$1 FOR UPDATE", request_id
        )
        if not row:
            raise HTTPException(404, "Solicitação não encontrada")
        if row["booking_id"]:
            status = {
                "analysis": "pending",
                "approved": "confirmed",
                "refused": "cancelled",
            }[body.status]
            await conn.execute(
                "UPDATE room_bookings SET status=$1 WHERE id=$2",
                status,
                row["booking_id"],
            )
        await conn.execute(
            "UPDATE professor_requests SET status=$1,staff_notes=$2 WHERE id=$3",
            body.status,
            body.staff_notes or None,
            request_id,
        )
        if body.status != row["status"] or (body.staff_notes or None) != row["staff_notes"]:
            kind = _REQUEST_KINDS.get(row["request_type"], row["request_type"])
            verb = {"analysis": "voltou para análise", "approved": "foi aprovada", "refused": "foi recusada"}[body.status]
            text = f"Sua solicitação de {kind} (nº {request_id}) {verb}."
            if body.staff_notes:
                text += f" Observação: {body.staff_notes}"
            await notify(conn, row["professor_id"], _REQUEST_TITLES[body.status], text, "reservations")
    return {"ok": True}


# ── Relatórios ───────────────────────────────────────────────────────────────


@router.get("/reports")
async def reports(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    user=Depends(_staff),
):
    """Agregados do período [from, to] (datas no fuso de Fortaleza; padrão: últimos 30 dias)."""
    to = to or datetime.now(LOCAL_TZ).date()
    from_ = from_ or to - timedelta(days=29)
    if to < from_:
        raise HTTPException(422, "Período inválido: a data final é anterior à inicial")
    if (to - from_).days > 366:
        raise HTTPException(422, "Período máximo de um ano")
    start = datetime.combine(from_, time.min, LOCAL_TZ)
    end = datetime.combine(to + timedelta(days=1), time.min, LOCAL_TZ)
    pool = get_pool()
    top_films = await pool.fetch(
        "SELECT f.id, f.title AS name, f.cover, COUNT(*) AS value FROM loans l JOIN copies c ON c.id = l.copy_id "
        "JOIN films f ON f.id = c.film_id WHERE l.borrowed_at >= $1 AND l.borrowed_at < $2 "
        "GROUP BY f.id, f.title, f.cover ORDER BY value DESC, name ASC LIMIT 10",
        start, end,
    )
    by_genre = await pool.fetch(
        "SELECT COALESCE(NULLIF(f.genre, ''), 'Sem gênero') AS name, COUNT(*) AS value FROM loans l "
        "JOIN copies c ON c.id = l.copy_id JOIN films f ON f.id = c.film_id "
        "WHERE l.borrowed_at >= $1 AND l.borrowed_at < $2 GROUP BY 1 ORDER BY value DESC, name ASC",
        start, end,
    )
    room_usage = await pool.fetch(
        "SELECT r.name, COUNT(b.room_id) AS value, "
        "COALESCE(ROUND((SUM(CASE WHEN b.room_id IS NULL THEN 0 ELSE EXTRACT(EPOCH FROM (LEAST(b.ends_at, $2) - GREATEST(b.starts_at, $1))) END) / 3600)::numeric, 1), 0)::float AS hours "
        "FROM rooms r LEFT JOIN (SELECT room_id, starts_at, ends_at FROM room_bookings WHERE status = 'confirmed' AND event_id IS NULL UNION ALL SELECT room_id, starts_at, ends_at FROM events WHERE room_id IS NOT NULL AND status IN ('published', 'finished')) b ON b.room_id = r.id "
        "AND b.starts_at < $2 AND b.ends_at > $1 "
        "WHERE r.status <> 'inactive' OR b.room_id IS NOT NULL GROUP BY r.id, r.name ORDER BY value DESC, r.name ASC",
        start, end,
    )
    by_day = await pool.fetch(
        "SELECT d::date AS date, COUNT(l.id) AS value FROM generate_series($3::date, $4::date, INTERVAL '1 day') d "
        "LEFT JOIN loans l ON (l.borrowed_at AT TIME ZONE 'America/Fortaleza')::date = d::date "
        "AND l.borrowed_at >= $1 AND l.borrowed_at < $2 GROUP BY d ORDER BY d",
        start, end, from_, to,
    )
    span = end - start
    totals = await _report_totals(pool, start, end)
    previous = await _report_totals(pool, start - span, start)
    equipment_usage = await pool.fetch(
        "SELECT e.name, COUNT(*) AS value FROM equipment_loans l JOIN equipment e ON e.id = l.equipment_id "
        "WHERE l.borrowed_at >= $1 AND l.borrowed_at < $2 GROUP BY e.name ORDER BY value DESC, e.name LIMIT 5",
        start, end,
    )
    room_requests = await pool.fetch(
        "SELECT status AS name, COUNT(*) AS value FROM professor_requests "
        "WHERE request_type = 'room' AND created_at >= $1 AND created_at < $2 GROUP BY status",
        start, end,
    )
    heat = await pool.fetch(
        """
        WITH days AS (SELECT d::date AS day FROM generate_series($1::date, $2::date, INTERVAL '1 day') d
                      WHERE extract(isodow FROM d) BETWEEN 1 AND 5),
             slots(slot, s, e) AS (VALUES ('07:30', TIME '07:30', TIME '09:10'), ('09:20', TIME '09:20', TIME '11:00'),
                                          ('11:20', TIME '11:20', TIME '13:00'), ('13:30', TIME '13:30', TIME '15:10'),
                                          ('15:30', TIME '15:30', TIME '17:10'), ('18:10', TIME '18:10', TIME '19:50'))
        SELECT r.name AS room, extract(isodow FROM d.day)::int AS dow, sl.slot,
               ROUND(100.0 * AVG(CASE WHEN EXISTS (
                 SELECT 1 FROM (SELECT room_id, starts_at, ends_at FROM room_bookings WHERE status = 'confirmed' AND event_id IS NULL UNION ALL SELECT room_id, starts_at, ends_at FROM events WHERE room_id IS NOT NULL AND status IN ('published', 'finished')) b WHERE b.room_id = r.id
                   AND b.starts_at < (d.day + sl.e) AT TIME ZONE 'America/Fortaleza'
                   AND b.ends_at > (d.day + sl.s) AT TIME ZONE 'America/Fortaleza') THEN 1 ELSE 0 END))::int AS pct
        FROM rooms r CROSS JOIN days d CROSS JOIN slots sl
        WHERE r.status <> 'inactive'
        GROUP BY r.name, dow, sl.slot ORDER BY r.name, sl.slot, dow
        """,
        from_, to,
    )
    projects = await pool.fetch(
        "SELECT CASE WHEN e.title ILIKE 'Vai de S%rie%' THEN 'Vai de Série' ELSE 'BibCine' END AS name, "
        "COUNT(DISTINCT e.id) AS sessions, "
        "COUNT(r.id) FILTER (WHERE r.present OR e.starts_at > NOW()) AS people "
        "FROM events e LEFT JOIN event_registrations r ON r.event_id = e.id "
        "WHERE e.status IN ('published', 'finished') AND e.starts_at >= $1 AND e.starts_at < $2 "
        "AND (e.title ILIKE 'BibCine%' OR e.title ILIKE 'Vai de S%rie%') GROUP BY 1 ORDER BY 1",
        start, end,
    )
    top_sessions = await pool.fetch(
        "SELECT e.id, e.title, e.starts_at, e.slots, COUNT(r.id) AS people FROM events e "
        "LEFT JOIN event_registrations r ON r.event_id = e.id "
        "WHERE e.status IN ('published', 'finished') AND e.starts_at >= $1 AND e.starts_at < $2 AND e.slots > 0 "
        "GROUP BY e.id ORDER BY COUNT(r.id)::float / e.slots DESC, people DESC, e.starts_at DESC LIMIT 3",
        start, end,
    )
    return {
        "from": from_.isoformat(),
        "to": to.isoformat(),
        "top_films": [dict(r) for r in top_films],
        "by_genre": [dict(r) for r in by_genre],
        "room_usage": [dict(r) for r in room_usage],
        "loans_by_day": [{"date": r["date"].isoformat(), "value": r["value"]} for r in by_day],
        "totals": totals,
        "previous": previous,
        "equipment_usage": [dict(r) for r in equipment_usage],
        "room_requests": [dict(r) for r in room_requests],
        "heat": [dict(r) for r in heat],
        "projects": [dict(r) for r in projects],
        "top_sessions": [dict(r) for r in top_sessions],
    }


async def _report_totals(pool, start, end) -> dict:
    """Indicadores do relatório num intervalo [start, end)."""
    row = await pool.fetchrow(
        "SELECT "
        "(SELECT COUNT(*) FROM loans WHERE borrowed_at >= $1 AND borrowed_at < $2) AS loans, "
        "(SELECT COUNT(*) FROM loans WHERE returned_at >= $1 AND returned_at < $2) AS returns, "
        "(SELECT COUNT(*) FROM loans WHERE due_at >= $1 AND due_at < $2 AND due_at < NOW() "
        " AND (returned_at IS NULL OR returned_at > due_at)) AS late, "
        "(SELECT COUNT(*) FROM reservations WHERE requested_at >= $1 AND requested_at < $2) AS reservations, "
        "(SELECT COUNT(*) FROM events WHERE status IN ('published', 'finished') AND starts_at >= $1 AND starts_at < $2) AS sessions, "
        "(SELECT COUNT(*) FROM professor_requests WHERE request_type = 'room' AND created_at >= $1 AND created_at < $2) AS room_requests, "
        "(SELECT COUNT(*) FROM equipment_requests WHERE created_at >= $1 AND created_at < $2) AS equipment_requests, "
        "(SELECT COUNT(*) FROM event_registrations r JOIN events e ON e.id = r.event_id "
        " WHERE e.status IN ('published', 'finished') AND e.starts_at >= $1 AND e.starts_at < $2 "
        " AND (r.present OR e.starts_at > NOW())) AS audience",
        start, end,
    )
    return dict(row)
