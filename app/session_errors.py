import asyncpg
from fastapi.responses import JSONResponse


def register_session_errors(app):
    @app.exception_handler(asyncpg.CheckViolationError)
    async def invalid_session(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Dados inválidos, filme indisponível ou capacidade da sala excedida"
            },
        )
