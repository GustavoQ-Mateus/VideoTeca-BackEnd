# API Videoteca UNIFOR — v1.0.0

Base: `https://<host>/api/v1` (local: `http://localhost:8000/api/v1`). Contrato completo em
[`docs/openapi.json`](openapi.json) — regenerar com `python scripts/export_openapi.py`
(o CI pode validar com `--check`). Em desenvolvimento a documentação interativa fica em `/docs`;
em produção `/docs`, `/redoc` e `/openapi.json` ficam desligados, salvo `EXPOSE_DOCS=true`.

## Autenticação

1. `POST /auth/login` com `{"login": "<e-mail ou matrícula>", "password": "...", "role": "aluno|professor|funcionario|admin"}`.
2. A resposta traz `token` (JWT HS256, validade `JWT_EXPIRE_MINUTES`, padrão 24 h) e `user`.
3. Envie `Authorization: Bearer <token>` nas rotas protegidas.

O login tem limite de **10 tentativas a cada 5 min por IP + login** (e 50 por IP); acima disso a
API responde `429` com `Retry-After`. Contas novas são criadas pela equipe em `POST /auth/register`
(funcionário cria aluno/professor; só admin cria funcionário/admin).

## Erros padrão

Todo erro tem o formato `{"detail": "<mensagem em pt-BR>"}`; erros de validação (`422` do Pydantic)
trazem `detail` como lista de campos. Nenhuma resposta expõe stack trace ou SQL.

| Código | Quando |
|---|---|
| 400 | Parâmetros com caracteres de controle (ex.: `%00`) ou regra simples violada |
| 401 | Sem token, token inválido/expirado ou credenciais inválidas |
| 403 | Perfil sem acesso à rota |
| 404 | Registro inexistente ou inativo |
| 409 | Conflito: sala ocupada, registro duplicado, pendência que impede a operação |
| 413 | Corpo acima de 11 MB (cartaz > 5 MB, comprovante > 10 MB) |
| 422 | Dados inválidos ou regra de negócio (ex.: publicação sem autorização de exibição) |
| 429 | Excesso de tentativas de login |
| 500 | Erro interno genérico (`"Erro interno. Tente novamente mais tarde."`) |
| 503 | Banco indisponível ou consulta acima do limite de 55 s |

Toda resposta inclui `X-Request-ID` (é aceito um enviado pelo cliente, 8–64 caracteres
`[A-Za-z0-9._-]`) e os headers de segurança `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy` (+ `Strict-Transport-Security` em produção).

## Endpoints por perfil

Legenda: **P** público · **A** aluno · **Pr** professor · **F** funcionário/admin · **T** qualquer autenticado.

| Perfil | Método e rota | Descrição |
|---|---|---|
| P | `GET /health` (fora de `/api/v1`) | Saúde da API (`SELECT 1` no banco; 503 se indisponível) |
| P | `POST /auth/login` | Login |
| T | `GET /auth/me` | Usuário autenticado |
| F | `POST /auth/register` | Cria conta |
| P | `GET /catalog/films?q=&limit=50&offset=0` | Catálogo (busca em título, título original, diretor e gênero; `limit` 1–100) |
| P | `GET /catalog/films/{film_id}` | Detalhe do filme |
| P | `GET /events`, `GET /events/{event_id}` | Sessões/eventos publicados |
| P | `GET /posters/{film_id}` | Cartaz (JPEG) |
| A | `GET /student/profile`, `/loans`, `/reservations`, `/events`, `/favorites`, `/notifications`, `/stats` | Painel do aluno |
| A | `POST /student/reservations`, `DELETE /student/reservations/{id}` | Reservar/cancelar filme |
| A | `POST`/`DELETE /student/events/{event_id}/enroll` | Inscrição em sessão |
| A | `PUT /student/notifications/{id}/read` | Marcar notificação como lida |
| Pr | `GET /professor/profile`, `/requests`, `/stats` | Painel do professor |
| Pr | `POST /professor/requests/film`, `POST /professor/requests/room` | Solicitações |
| Pr | `POST /reservas`, `GET /reservas/me` | Reserva de sala |
| Pr, F | `GET /salas/disponibilidade?starts_at=&ends_at=&capacity=&resources=` | Salas livres no período |
| F | `GET /staff/stats`, `/inventory`, `/loans`, `/reservations`, `/users`, `/rooms`, `/events`, `/requests` | Painel do funcionário |
| F | `GET/POST /staff/films`, `PUT/DELETE /staff/films/{film_id}`, `POST /staff/films/{film_id}/poster` | Acervo, cartaz e autorização de exibição |
| F | `GET/POST /staff/films/{film_id}/rights-proofs`, `GET/DELETE …/rights-proofs/{proof_id}` | Comprovantes de autorização |
| F | `GET/POST /staff/sessions`, `PUT /staff/sessions/{id}`, `PUT /staff/sessions/{id}/publication` | Sessões e publicação |
| F | `GET/POST /staff/equipment`, `GET/POST /staff/equipment/loans`, `POST …/loans/{id}/return`, `GET /staff/equipment/incidents` | Equipamentos |
| T | `GET /me/privacy`, `POST /me/privacy/consent` | Consentimento da política (versão `2026-10`) |
| T | `GET /me/data` | Exporta os dados pessoais (`meus-dados.json`) |
| T | `POST /me/deletion` | Exclusão/anonimização da conta (409 se houver pendências) |

### Direitos autorais de exibição

`POST/PUT /staff/films` aceitam `rights_status` (`pending`, `authorized`, `denied`, `public_domain`),
`rights_holder` (≤ 200), `license_type` (≤ 80), `rights_valid_until` (data ou `null`) e
`rights_notes` (≤ 2000). Os campos formam um grupo: no `PUT`, omitir `rights_status` mantém a
autorização gravada. `authorized` exige titular e validade não vencida. A publicação de sessão
(`PUT /staff/sessions/{id}/publication` com `published=true`) exige `authorized` ou `public_domain`
válido até o fim da sessão e, para `authorized`, ao menos um comprovante. Revogar a autorização ou
remover o último comprovante com sessões futuras publicadas responde `409`.

Comprovantes: `multipart/form-data` com campo `file`; o tipo é detectado pelos bytes iniciais (PDF,
PNG ou JPEG), o nome é sanitizado (máx. 120 caracteres) e o download sai com
`Content-Disposition: attachment`, `X-Content-Type-Options: nosniff` e `Cache-Control: private, no-store`.

## Exemplos (curl)

```bash
API=http://localhost:8000/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"login":"FUNC001","password":"<senha>","role":"funcionario"}' | jq -r .token)

# Catálogo público com busca e paginação
curl -s "$API/catalog/films?q=bacurau&limit=20&offset=0"

# Marcar filme como autorizado e anexar comprovante
curl -s -X PUT $API/staff/films/6 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Bacurau","rights_status":"authorized","rights_holder":"Vitrine Filmes","license_type":"exibicao_publica","rights_valid_until":"2027-12-31"}'
curl -s -X POST $API/staff/films/6/rights-proofs -H "Authorization: Bearer $TOKEN" -F 'file=@contrato.pdf'

# Agendar e publicar sessão
SID=$(curl -s -X POST $API/staff/sessions -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"Cineclube","film_id":6,"room_id":1,"slots":30,"starts_at":"2026-11-20T19:00:00-03:00","ends_at":"2026-11-20T21:30:00-03:00"}' | jq .id)
curl -s -X PUT $API/staff/sessions/$SID/publication -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"published":true}'

# LGPD: exportar meus dados
curl -s -OJ $API/me/data -H "Authorization: Bearer $TOKEN"
```

PowerShell:

```powershell
$api = "http://localhost:8000/api/v1"
$login = Invoke-RestMethod -Method Post "$api/auth/login" -ContentType "application/json" `
  -Body '{"login":"FUNC001","password":"<senha>","role":"funcionario"}'
Invoke-RestMethod "$api/staff/stats" -Headers @{ Authorization = "Bearer $($login.token)" }
```
