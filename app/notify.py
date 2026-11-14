"""Notificações in-app e parâmetros do sistema, compartilhados pelos routers.

Categorias de notificação (respeitam as preferências em notification_prefs):
  "reservations" — reservas e empréstimos; "events" — sessões e eventos; "reminders" — lembretes de prazo.
"""

CATEGORIES = ("reservations", "events", "reminders")
DEFAULT_SETTINGS = {"loan_days": 7, "max_items_per_user": 3, "max_reservations": 2, "reservation_pickup_days": 3}


async def notify(conn, user_id, title: str, body: str = "", category: str = "reservations") -> bool:
    """Cria uma notificação para o usuário, salvo se ele desligou a categoria. Retorna se criou."""
    if user_id is None:
        return False
    if category in CATEGORIES:
        # notification_prefs é criada na migration 014; sem linha = tudo ligado.
        enabled = await conn.fetchval(
            f"SELECT {category} FROM notification_prefs WHERE user_id = $1", user_id
        )
        if enabled is False:
            return False
    await conn.execute(
        "INSERT INTO notifications (user_id, title, body) VALUES ($1, $2, $3)", user_id, title, body
    )
    return True


async def get_settings(conn) -> dict:
    """Parâmetros de empréstimo e reserva (tabela app_settings, linha única id=1)."""
    row = await conn.fetchrow(
        "SELECT loan_days, max_items_per_user, max_reservations, reservation_pickup_days FROM app_settings WHERE id = 1"
    )
    return {**DEFAULT_SETTINGS, **(dict(row) if row else {})}
