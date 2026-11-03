"""Metadados OpenAPI da API (T24.2)."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

VERSION = "1.0.0"
DESCRIPTION = """
API da **Videoteca UNIFOR**: catálogo de filmes, sessões de exibição, reservas de sala,
empréstimo de equipamentos, direitos autorais de exibição e privacidade (LGPD).

**Autenticação:** `POST /api/v1/auth/login` devolve um JWT; envie-o em
`Authorization: Bearer <token>`. O perfil (`aluno`, `professor`, `funcionario`, `admin`)
define as rotas permitidas — 401 sem token/token inválido, 403 para perfil sem acesso.

**Erros:** sempre `{"detail": "<mensagem em pt-BR>"}` (validação 422 traz `detail` como lista).
Cada resposta inclui `X-Request-ID` para correlação com os logs.
""".strip()

TAGS = [
    {"name": "health", "description": "Verificação de disponibilidade (sem autenticação)."},
    {"name": "auth", "description": "Login, perfil autenticado e criação de contas pela equipe."},
    {"name": "catalog", "description": "Catálogo público de filmes com busca e paginação."},
    {"name": "events", "description": "Sessões e eventos publicados (público)."},
    {"name": "student", "description": "Área do aluno: reservas de filmes, empréstimos, inscrições e notificações."},
    {"name": "professor", "description": "Área do professor: solicitações de filmes e salas."},
    {"name": "rooms", "description": "Disponibilidade de salas e reservas de sala pelo professor."},
    {"name": "staff", "description": "Painel do funcionário: acervo, empréstimos, reservas, usuários e solicitações."},
    {"name": "films", "description": "Cadastro de filmes, cartazes e autorização de exibição (funcionário/admin)."},
    {"name": "rights", "description": "Comprovantes de autorização de exibição (PDF, PNG ou JPEG, até 10 MB)."},
    {"name": "sessions", "description": "Sessões de exibição: agendamento e publicação condicionada aos direitos."},
    {"name": "equipment", "description": "Equipamentos, retiradas, devoluções e ocorrências."},
    {"name": "privacy", "description": "LGPD: consentimento, exportação e exclusão dos dados do titular."},
]


def configure(app: FastAPI):
    app.title = "Videoteca UNIFOR API"
    app.version = VERSION
    app.description = DESCRIPTION
    app.contact = {"name": "Equipe Videoteca UNIFOR", "url": "https://github.com/GustavoQ-Mateus/VideoTeca-BackEnd"}
    app.openapi_tags = TAGS
    app.openapi_schema = None

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            contact=app.contact,
        )
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["HTTPBearer"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Token obtido em POST /api/v1/auth/login.",
        }
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
