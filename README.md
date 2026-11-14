# VideoTeca-BackEnd

API do sistema de gestão da **Videoteca UNIFOR** — versão **1.0.0** ([CHANGELOG](CHANGELOG.md)).

- **Stack:** FastAPI (Python 3.12) + PostgreSQL (asyncpg) + JWT (python-jose)
- **Produção:** AWS ECS Fargate atrás de ALB (T28), banco em Amazon Aurora Serverless v2 (T26), deploy por GitHub Actions + OIDC (T24.3)

## Documentação

| Documento | Conteúdo |
|-----------|----------|
| [docs/API.md](docs/API.md) · [docs/openapi.json](docs/openapi.json) | Endpoints por perfil, autenticação, erros, exemplos |
| [docs/RELEASE.md](docs/RELEASE.md) | Runbook de produção: custo, Terraform, migrations, deploy, smoke test, rollback, destroy |
| [infra/README.md](infra/README.md) | Infraestrutura AWS (Terraform) e flag de custo `enable_runtime` |
| [db/DER.md](db/DER.md) | Diagrama ER e dicionário de dados |
| [docs/sprints-3-5.md](docs/sprints-3-5.md) | Regras de reservas, equipamentos, cartazes e sessões |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Fluxo de branches e PRs |

## Estrutura

```
app/
├── main.py            # bootstrap FastAPI, middlewares, routers, /health
├── core/              # config, segurança/JWT, hardening, observabilidade, OpenAPI
├── db/postgres.py     # pool asyncpg
└── routers/           # auth, catalog, events, student, professor, staff, film_rights, privacy…
db/
├── migrations/        # 001–012, aplicadas em ordem (idempotentes)
├── seed/seed_dev.sql  # massa de teste (dev) — senha: videoteca123
└── DER.md
infra/                 # Terraform: rede, Aurora, ECS/ALB, observabilidade, alarmes, budget, OIDC
.github/workflows/     # ci.yml (testes + terraform) · deploy.yml (produção)
```

## Rodar localmente

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env      # ajuste DATABASE_URL, JWT_SECRET, CORS_ORIGINS
uvicorn app.main:app --reload
```

Ou via Docker (Postgres + API + nginx na porta 80):

```powershell
Copy-Item .env.example .env      # defina POSTGRES_PASSWORD
docker compose up --build
```

Na primeira subida (volume vazio) o Postgres aplica as migrations 001–012 e o
seed de desenvolvimento. Banco já existente: aplique as migrations novas
manualmente (ver [RELEASE.md § Migrations](docs/RELEASE.md#6-migrations)).

Health check: `GET /health` · Docs interativas: `GET /docs` (desligáveis em produção).

## Testes

```powershell
pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL = "postgresql://postgres@127.0.0.1:55435/videoteca_test"  # banco exclusivo *_test
python -m pytest -q

terraform -chdir=infra init -backend=false
terraform -chdir=infra test       # provider simulado, não toca na AWS
```

## Deploy em produção

Resumo (detalhes em [docs/RELEASE.md](docs/RELEASE.md)):

1. `terraform apply -var-file=prod.tfvars` em `infra/` (a partir de `prod.tfvars.example`).
2. Cadastrar no environment GitHub `production` as variáveis do output
   `github_deploy_settings`, `CORS_ORIGINS` e `API_BASE_URL`, com revisor obrigatório.
3. `git tag -a v1.0.0 -m "..."; git push origin v1.0.0` → workflow **Deploy produção**:
   testes → aprovação → build/push por digest → migrations → rolling deploy → smoke test.

Rollback: automático pelo circuit breaker do ECS; manual com a task definition anterior.

## Variáveis de ambiente

| Variável | Descrição |
|----------|-----------|
| `DATABASE_URL` | DSN do Postgres (`postgresql://user:senha@host:5432/videoteca`) |
| `CORS_ORIGINS` | Origens liberadas, separadas por vírgula (sem `*` em produção) |
| `JWT_SECRET` | **Obrigatório em produção** — ≥ 32 caracteres |
| `JWT_EXPIRE_MINUTES` | Validade do token (padrão 1440 = 24h) |
| `ENVIRONMENT` | `development` (padrão) ou `production` (ativa validações, HSTS e métricas EMF) |

> ⚠️ Nunca commitar `.env`, `*.pem`, `*.tfstate`, `prod.tfvars` ou chaves. Em produção os
> segredos vêm do AWS Secrets Manager e o deploy usa OIDC (sem access keys).
