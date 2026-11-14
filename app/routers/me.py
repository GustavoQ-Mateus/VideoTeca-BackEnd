"""Dados do próprio usuário autenticado (qualquer perfil): perfil, preferências e notificações."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import current_user
from app.db.postgres import get_pool

router = APIRouter()
PROFILE = "SELECT p.id, u.email, p.full_name, p.role, p.registration, p.course, p.phone FROM profiles p JOIN users u ON u.id = p.id WHERE p.id = $1"


class ProfileUpdate(BaseModel):
    # E-mail e matrícula/SIAPE não são editáveis aqui: campos extras são recusados (422).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str | None = Field(None, min_length=3, max_length=120)
    course: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30, pattern=r"^[0-9()+\- ]*$")


class NotificationPrefs(BaseModel):
    reservations: bool
    events: bool
    reminders: bool


@router.get("/profile")
async def get_profile(user=Depends(current_user)):
    return dict(await get_pool().fetchrow(PROFILE, user["id"]))


@router.put("/profile")
async def update_profile(body: ProfileUpdate, user=Depends(current_user)):
    changes = body.model_dump(exclude_unset=True)
    if "full_name" in changes and not changes["full_name"]:
        raise HTTPException(422, "Informe o nome completo")
    pool = get_pool()
    if changes:
        # Campos vazios em curso/telefone limpam o valor.
        await pool.execute(
            "UPDATE profiles SET full_name = CASE WHEN $2 THEN $3 ELSE full_name END, "
            "course = CASE WHEN $4 THEN NULLIF($5, '') ELSE course END, "
            "phone = CASE WHEN $6 THEN NULLIF($7, '') ELSE phone END WHERE id = $1",
            user["id"], "full_name" in changes, changes.get("full_name"),
            "course" in changes, changes.get("course"), "phone" in changes, changes.get("phone"),
        )
    return dict(await pool.fetchrow(PROFILE, user["id"]))


@router.get("/notification-prefs")
async def get_prefs(user=Depends(current_user)):
    row = await get_pool().fetchrow(
        "SELECT reservations, events, reminders FROM notification_prefs WHERE user_id = $1", user["id"]
    )
    return dict(row) if row else {"reservations": True, "events": True, "reminders": True}


@router.put("/notification-prefs")
async def put_prefs(body: NotificationPrefs, user=Depends(current_user)):
    row = await get_pool().fetchrow(
        "INSERT INTO notification_prefs (user_id, reservations, events, reminders) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id) DO UPDATE SET reservations = EXCLUDED.reservations, events = EXCLUDED.events, "
        "reminders = EXCLUDED.reminders RETURNING reservations, events, reminders",
        user["id"], body.reservations, body.events, body.reminders,
    )
    return dict(row)


@router.get("/notifications")
async def list_notifications(
    unread: bool = Query(False, description="true = só as não lidas"),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
):
    rows = await get_pool().fetch(
        "SELECT id, title, body, read, created_at FROM notifications "
        "WHERE user_id = $1 AND (NOT $2 OR read = false) ORDER BY created_at DESC, id DESC LIMIT $3",
        user["id"], unread, limit,
    )
    return [dict(r) for r in rows]


@router.get("/notifications/unread-count")
async def unread_count(user=Depends(current_user)):
    count = await get_pool().fetchval(
        "SELECT COUNT(*) FROM notifications WHERE user_id = $1 AND read = false", user["id"]
    )
    return {"count": count}


async def mark_notification_read(notif_id: int, user):
    updated = await get_pool().fetchval(
        "UPDATE notifications SET read = true WHERE id = $1 AND user_id = $2 RETURNING id", notif_id, user["id"]
    )
    if not updated:
        raise HTTPException(404, "Notificação não encontrada")
    return {"ok": True}


@router.put("/notifications/{notif_id}/read")
async def mark_read(notif_id: int, user=Depends(current_user)):
    return await mark_notification_read(notif_id, user)
