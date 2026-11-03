"""LGPD (T22.2): consentimento, portabilidade (exportação) e eliminação/anonimização dos dados."""

import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from app.core.security import current_user, hash_password
from app.db.postgres import get_pool

router = APIRouter(prefix="/me")
POLICY_VERSION = "2026-10"
ANON_NAME = "Usuário removido"
# Each query returns only rows owned by $1 (the authenticated user).
EXPORT_QUERIES = {
    "reservas_filmes": "SELECT r.id,r.status,r.requested_at,r.pickup_until,f.title AS filme FROM reservations r LEFT JOIN films f ON f.id=r.film_id WHERE r.user_id=$1 ORDER BY r.id",
    "emprestimos_filmes": "SELECT l.id,l.status,l.borrowed_at,l.due_at,l.returned_at,f.title AS filme FROM loans l LEFT JOIN copies c ON c.id=l.copy_id LEFT JOIN films f ON f.id=c.film_id WHERE l.user_id=$1 ORDER BY l.id",
    "emprestimos_equipamentos": "SELECT l.id,e.name AS equipamento,l.quantity,l.borrowed_at,l.due_at,l.returned_at,l.condition FROM equipment_loans l JOIN equipment e ON e.id=l.equipment_id WHERE l.user_id=$1 ORDER BY l.id",
    "reservas_salas": "SELECT b.id,r.name AS sala,b.title,b.starts_at,b.ends_at,b.status,b.course,b.registration,b.student_count,b.notes FROM room_bookings b LEFT JOIN rooms r ON r.id=b.room_id WHERE b.professor_id=$1 ORDER BY b.id",
    "solicitacoes": "SELECT id,request_type,payload,status,staff_notes,created_at FROM professor_requests WHERE professor_id=$1 ORDER BY id",
    "inscricoes_eventos": "SELECT er.event_id,e.title,e.starts_at,er.present FROM event_registrations er JOIN events e ON e.id=er.event_id WHERE er.user_id=$1 ORDER BY er.id",
    "notificacoes": "SELECT id,title,body,read,created_at FROM notifications WHERE user_id=$1 ORDER BY id",
    "consentimentos": "SELECT policy_version,accepted_at FROM privacy_consents WHERE user_id=$1 ORDER BY accepted_at",
    "solicitacoes_lgpd": "SELECT id,kind,status,created_at,resolved_at FROM data_requests WHERE user_id=$1 ORDER BY id",
}
# Pending obligations that prevent erasure (art. 16 LGPD: cumprimento de obrigação/contrato em curso).
BLOCKERS = (
    ("empréstimo de filme em aberto", "SELECT EXISTS(SELECT 1 FROM loans WHERE user_id=$1 AND returned_at IS NULL AND status<>'returned')"),
    ("retirada de equipamento em aberto", "SELECT EXISTS(SELECT 1 FROM equipment_loans WHERE user_id=$1 AND returned_at IS NULL)"),
    ("reserva de filme ativa", "SELECT EXISTS(SELECT 1 FROM reservations WHERE user_id=$1 AND status IN ('waiting','ready'))"),
    ("reserva de sala futura", "SELECT EXISTS(SELECT 1 FROM room_bookings WHERE professor_id=$1 AND status<>'cancelled' AND ends_at>NOW())"),
    ("inscrição em sessão futura", "SELECT EXISTS(SELECT 1 FROM event_registrations er JOIN events e ON e.id=er.event_id WHERE er.user_id=$1 AND e.status='published' AND COALESCE(e.ends_at,e.starts_at)>NOW())"),
)


class Consent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    policy_version: str = Field(min_length=1, max_length=20)


@router.get("/privacy")
async def privacy(user=Depends(current_user)):
    accepted = await get_pool().fetchval(
        "SELECT accepted_at FROM privacy_consents WHERE user_id=$1 AND policy_version=$2",
        user["id"],
        POLICY_VERSION,
    )
    return {"policy_version": POLICY_VERSION, "accepted_at": accepted}


@router.post("/privacy/consent")
async def consent(body: Consent, user=Depends(current_user)):
    if body.policy_version != POLICY_VERSION:
        raise HTTPException(422, "Versão da política desatualizada; recarregue a página")
    accepted = await get_pool().fetchval(
        """INSERT INTO privacy_consents(user_id,policy_version) VALUES($1,$2)
        ON CONFLICT(user_id,policy_version) DO UPDATE SET policy_version=EXCLUDED.policy_version
        RETURNING accepted_at""",
        user["id"],
        POLICY_VERSION,
    )
    return {"policy_version": POLICY_VERSION, "accepted_at": accepted}


@router.get("/data")
async def export_data(user=Depends(current_user)):
    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO data_requests(user_id,kind,status,resolved_at) VALUES($1,'export','done',NOW())",
            user["id"],
        )
        profile = await conn.fetchrow(
            """SELECT p.id,p.full_name,p.role,p.registration,p.course,p.phone,p.created_at,u.email,
            u.created_at AS account_created_at FROM profiles p JOIN users u ON u.id=p.id WHERE p.id=$1""",
            user["id"],
        )
        data = {
            "gerado_em": datetime.now(timezone.utc),
            "politica_privacidade": POLICY_VERSION,
            "perfil": dict(profile),
        }
        for key, sql in EXPORT_QUERIES.items():
            data[key] = [dict(r) for r in await conn.fetch(sql, user["id"])]
    for item in data["solicitacoes"]:
        if isinstance(item["payload"], str):
            item["payload"] = json.loads(item["payload"])
    return Response(
        json.dumps(jsonable_encoder(data), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=meus-dados.json",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/deletion")
async def deletion(user=Depends(current_user)):
    async with get_pool().acquire() as conn, conn.transaction():
        # Serializes with concurrent requests of the same user.
        await conn.execute("SELECT 1 FROM profiles WHERE id=$1 FOR UPDATE", user["id"])
        reasons = [label for label, sql in BLOCKERS if await conn.fetchval(sql, user["id"])]
        if reasons:
            raise HTTPException(
                409,
                "Não é possível excluir a conta enquanto houver: " + ", ".join(reasons),
            )
        uid = user["id"]
        await conn.execute(
            "UPDATE users SET email=$2,password_hash=$3 WHERE id=$1",
            uid,
            f"removido-{uid}@anon.invalid",
            hash_password(secrets.token_urlsafe(32)),
        )
        await conn.execute(
            "UPDATE profiles SET full_name=$2,registration=NULL,course=NULL,phone=NULL WHERE id=$1",
            uid,
            ANON_NAME,
        )
        # History stays (FKs intact) without identifying free-text data.
        await conn.execute(
            "UPDATE room_bookings SET registration=NULL,course=NULL,notes=NULL WHERE professor_id=$1", uid
        )
        await conn.execute(
            """UPDATE professor_requests SET payload=payload-'registration'-'course'-'notes'
            WHERE professor_id=$1 AND jsonb_typeof(payload)='object'""",
            uid,
        )
        await conn.execute("DELETE FROM notifications WHERE user_id=$1", uid)
        await conn.execute(
            "INSERT INTO data_requests(user_id,kind,status,resolved_at) VALUES($1,'deletion','done',NOW())",
            uid,
        )
    return {"ok": True}
