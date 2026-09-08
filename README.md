# VideoTeca-BackEnd

API do sistema de gestão da **Videoteca UNIFOR**.

- **Stack:** FastAPI (Python) + PostgreSQL (asyncpg) + JWT (python-jose)
- **Alvo de deploy:** AWS Fargate (T28), banco em Amazon Aurora (T26)

## Estrutura

```
app/
├── main.py            # bootstrap FastAPI, CORS, inclusão de routers
├── core/
│   ├── config.py      # settings via variáveis de ambiente (.env)
│   └── security.py    # hashing de senha e JWT
├── db/
│   └── postgres.py    # pool asyncpg
└── routers/
    ├── auth.py        # /api/v1/auth   — login, me
    ├── catalog.py     # /api/v1/catalog — filmes públicos
    ├── events.py      # /api/v1/events
    ├── student.py     # /api/v1/student
    ├── professor.py   # /api/v1/professor
    └── staff.py       # /api/v1/staff  — painel do funcionário
init.sql               # schema + massa inicial (dev)
```

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # ajuste DATABASE_URL, JWT_SECRET, CORS_ORIGINS
uvicorn app.main:app --reload
```

Ou via Docker (sobe Postgres + API + nginx):

```bash
cp .env.example .env        # defina POSTGRES_PASSWORD
docker compose up --build
```

Health check: `GET /health` · Docs interativas: `GET /docs`

## Variáveis de ambiente

| Variável | Descrição |
|----------|-----------|
| `DATABASE_URL` | DSN do Postgres (`postgresql://user:senha@host:5432/videoteca`) |
| `CORS_ORIGINS` | Origens liberadas, separadas por vírgula |
| `JWT_SECRET` | **Obrigatório em produção** — segredo de assinatura do JWT |
| `JWT_EXPIRE_MINUTES` | Validade do token (padrão 1440 = 24h) |

> ⚠️ Nunca commitar `.env`, `*.pem` ou chaves. Em produção os segredos vêm do AWS Secrets Manager (T25).
