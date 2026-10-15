from datetime import datetime, timezone
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, model_validator


class Period(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def valid_period(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("O fim deve ser posterior ao início")
        if (self.ends_at - self.starts_at).total_seconds() > 86400:
            raise ValueError("O período máximo é de 24 horas")
        return self


def future_period(period):
    if period.starts_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "Escolha um horário futuro")


async def lock_room(conn, room_id, capacity=1):
    room = await conn.fetchrow("SELECT * FROM rooms WHERE id=$1 FOR UPDATE", room_id)
    if not room:
        raise HTTPException(404, "Sala não encontrada")
    if room["status"] != "available":
        raise HTTPException(409, "Sala indisponível")
    if room["capacity"] is not None and capacity > room["capacity"]:
        raise HTTPException(422, "Quantidade de alunos excede a capacidade da sala")
    return room
