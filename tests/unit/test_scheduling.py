from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.bookings import BookingCreate
from app.routers.sessions import SessionInput
from app.scheduling import Period, future_period

NOW = datetime.now(timezone.utc)


def period(start, hours):
    return {"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=hours)).isoformat()}


def test_period_rules():
    assert Period(**period(NOW, 24))
    for hours in (0, -1, 24.01):
        with pytest.raises(ValidationError):
            Period(**period(NOW, hours))
    with pytest.raises(ValidationError):  # naive datetimes are ambiguous
        Period(starts_at="2030-01-01T10:00:00", ends_at="2030-01-01T11:00:00")


def test_future_period():
    now = datetime.now(timezone.utc)
    future_period(Period(**period(now + timedelta(minutes=5), 1)))
    with pytest.raises(HTTPException) as exc:
        future_period(Period(**period(now - timedelta(minutes=1), 1)))
    assert exc.value.status_code == 422


def test_session_and_booking_inputs():
    base = period(datetime.now(timezone.utc) + timedelta(days=1), 2)
    session = SessionInput(title="  Mostra  ", film_id=1, room_id=1, slots=10, **base)
    assert session.title == "Mostra"
    for bad in ({"slots": 0}, {"film_id": 0}, {"title": " "}, {"description": "x" * 4001}):
        with pytest.raises(ValidationError):
            SessionInput(**({"title": "Mostra", "film_id": 1, "room_id": 1, "slots": 10} | bad), **base)
    booking = {"room_id": 1, "title": "Aula", "course": "Cinema", "registration": "1", "student_count": 1}
    assert BookingCreate(**booking, **base).student_count == 1
    with pytest.raises(ValidationError):
        BookingCreate(**(booking | {"title": "ab"}), **base)
