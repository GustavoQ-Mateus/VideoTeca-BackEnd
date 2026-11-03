from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from fastapi.exceptions import RequestValidationError
from app.core.security import require_role
from app.db.postgres import get_pool
from app.scheduling import Period

router = APIRouter()


@router.get("/salas/disponibilidade")
async def availability(
    starts_at: datetime,
    ends_at: datetime,
    capacity: int = Query(1, ge=1, le=10000),
    resources: list[str] = Query(default=[]),
    user=Depends(require_role("professor", "funcionario", "admin")),
):
    try:
        Period(starts_at=starts_at, ends_at=ends_at)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    pool = get_pool()
    # One snapshot for rooms and occupancy. Intervals are half-open: adjacent bookings are valid.
    rows = await pool.fetch(
        """
        SELECT r.id, r.name, r.capacity, r.equipment,
               b.starts_at AS busy_start, b.ends_at AS busy_end
        FROM rooms r LEFT JOIN room_bookings b ON b.room_id=r.id
          AND b.status <> 'cancelled' AND b.starts_at < $2 AND b.ends_at > $1
        WHERE r.status='available' AND r.capacity >= $3
          AND COALESCE(r.equipment, ARRAY[]::text[]) @> $4::text[]
        ORDER BY r.name, b.starts_at
    """,
        starts_at,
        ends_at,
        capacity,
        resources,
    )
    rooms = {}
    for row in rows:
        room = rooms.setdefault(
            row["id"], {k: row[k] for k in ("id", "name", "capacity", "equipment")}
        )
        room.setdefault("busy", [])
        if row["busy_start"]:
            room["busy"].append((row["busy_start"], row["busy_end"]))
    for room in rooms.values():
        cursor, free = starts_at, []
        for start, end in room.pop("busy"):
            if start > cursor:
                free.append({"starts_at": cursor, "ends_at": min(start, ends_at)})
            cursor = max(cursor, end)
        if cursor < ends_at:
            free.append({"starts_at": cursor, "ends_at": ends_at})
        room["free_slots"] = free
        room["available"] = (
            len(free) == 1
            and free[0]["starts_at"] == starts_at
            and free[0]["ends_at"] == ends_at
        )
    return list(rooms.values())
