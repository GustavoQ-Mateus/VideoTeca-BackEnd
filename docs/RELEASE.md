# Runbook de publicação — Videoteca API (produção)

Tarefa **T24.3**. Cobre: pré-requisitos, custo, ordem de `terraform apply`,
migrations, deploy pelo GitHub Actions, smoke test, rollback e destroy.
Comandos em **PowerShell** (padrão do time).

> Conta AWS pessoal `765656213653`, profile **`prdal-gustavo`**. Nunca use o
> profile `default`. Todo recurso pago cobra até o `terraform destroy`.

## 1. Visão geral

```
GitHub (tag v*)  ──▶  deploy.yml ──[aprovação "production"]──▶ OIDC ──▶ role github-deploy
                                   │
                                   ├─ build → ECR (imagem por digest sha256)
                                   ├─ task definition nova (ENVIRONMENT=production, CORS)
                                   ├─ migrations: task one-off com a imagem nova
                                   └─ update-service (rolling + circuit breaker/rollback)
Amplify (front, HTTPS) ──▶ ALB ──▶ ECS Fargate (API) ──▶ Aurora Serverless v2
CloudWatch: logs JSON + EMF, dashboard, alarmes → SNS e-mail; Budget + Anomaly Detection
```

| Peça | Onde |
|------|------|
| Infra base, Aurora, ECS, ALB | `infra/*.tf` (`enable_runtime = true`) |
| Observabilidade / alarmes / custo | `infra/observability.tf`, `infra/alarms.tf`, `infra/budget.tf` |
| Role de deploy (OIDC) | `infra/github_oidc.tf` |
| Pipeline | `.github/workflows/deploy.yml` |
| Front | repositório FrontEnd, `amplify.yml` e `docs/DEPLOY.md` |

## 2. Pré-requisitos

- Terraform ≥ 1.5 (CI usa 1.15.8), AWS CLI v2, Docker (só para teste local).
- Acesso de admin no repositório GitHub (environments e variables).
- E-mail para alarmes e budget (as assinaturas precisam ser **confirmadas** no
  link que a AWS envia).
- Testes verdes: `python -m pytest -q` e `terraform -chdir=infra test`.

## 3. Custo estimado (us-east-1)

| Item | Ligado (`enable_runtime=true`) | Desligado |
|------|------|------|
| ALB (hora + LCU mínima) | ~US$ 17/mês | — |
| IPv4 públicos (ALB 2 + 1 task) | ~US$ 11/mês | — |
| Fargate 0,25 vCPU / 0,5 GB × 1 task | ~US$ 9/mês | — |
| Aurora Serverless v2 (writer + reader, min 0 ACU) | ~US$ 0,12/ACU-hora ativo por instância + storage ~US$ 0,10/GB | — |
| Secrets Manager (3 segredos) | US$ 1,20/mês | US$ 1,20/mês |
| CloudWatch: 8 alarmes + 3 métricas de log + métricas EMF por rota | ~US$ 2–3/mês + US$ 0,30 por métrica EMF (2 por rota ativa) | — |
| Dashboard (até 3 por conta) / SNS e-mail / Budgets (2) / Anomaly Detection | grátis | grátis |
| ECR (≤ 5 imagens) | centavos | centavos |

Ordem de grandeza: **US$ 40–60/mês ligado** (depende de quanto o Aurora fica
ativo e de quantas rotas publicam métricas EMF); ~US$ 1,50/mês desligado.
Por isso o budget de produção sugerido em `prod.tfvars.example` é US$ 40
(o default da variável, US$ 20, serve para a conta de dev).

## 4. Primeira publicação

### 4.1 Variáveis

```powershell
cd infra
Copy-Item prod.tfvars.example prod.tfvars   # edite e-mails, repositório, limites
```

`prod.tfvars` pode conter e-mails reais: não commite. Use um **state separado**
para produção (outro diretório ou `terraform workspace new prod`), porque
`environment = "prod"` muda o nome de todos os recursos.

### 4.2 Ordem do apply

```powershell
$env:AWS_PROFILE = "prdal-gustavo"
terraform init

# (a) Fundação grátis + budget + OIDC (sem runtime): valide o custo zero primeiro.
terraform apply -var-file=prod.tfvars -var enable_runtime=false

# (b) Primeira imagem no ECR (a task definition do Terraform aponta para api_image_tag).
$repo = terraform output -raw ecr_repository_url
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $repo.Split('/')[0]
docker build --platform linux/amd64 -t "${repo}:v1.0.0" ..
docker push "${repo}:v1.0.0"

# (c) Runtime pago: Aurora (~10 min), ECS, ALB, dashboard, alarmes.
terraform apply -var-file=prod.tfvars
terraform output alb_url
terraform output github_deploy_settings
terraform output cloudwatch_dashboard_url
```

Confirme as assinaturas de e-mail (SNS de alarmes e Budgets).

### 4.3 GitHub

Settings → Environments → **production**:

- **Required reviewers**: pelo menos 1 pessoa (aprovação manual do deploy).
- **Deployment branches and tags**: só tags `v*` (e `main`, se quiser dispatch).
- **Variables** (não são segredos; nenhuma chave AWS é cadastrada):

| Variável | Valor |
|----------|-------|
| `AWS_REGION`, `AWS_ROLE_ARN`, `ECR_REPOSITORY`, `ECS_CLUSTER`, `ECS_SERVICE`, `ECS_TASK_FAMILY` | `terraform output github_deploy_settings` |
| `CORS_ORIGINS` | URL HTTPS do front (Amplify/domínio). Não pode conter `*`. |
| `API_BASE_URL` | URL pública da API (usada no smoke test) |

A role só aceita tokens com `sub = repo:<owner>/<repo>:environment:production`:
branches, PRs e forks não conseguem assumi-la.

## 5. Publicar uma versão

```powershell
git checkout main; git pull
# atualize CHANGELOG.md e a versão (app/core/openapi.py)
git tag -a v1.0.0 -m "Videoteca 1.0.0"
git push origin v1.0.0
```

O workflow **Deploy produção** roda os testes e aguarda aprovação. Aprovado:

1. Build `linux/amd64` e push no ECR com tags `<sha>` e `v1.0.0`; o deploy usa
   o **digest** (`repo@sha256:…`), imutável.
2. Registra nova revisão da task definition (copiada da ativa) com imagem,
   `ENVIRONMENT=production` e `CORS_ORIGINS`. Segredos (`DATABASE_URL`,
   `JWT_SECRET`) continuam vindo do Secrets Manager.
3. **Migrations** (seção 6).
4. `update-service` com circuit breaker + rollback; espera `services-stable` e
   confere que a revisão nova é a primária com `rolloutState = COMPLETED`.
5. Smoke test (`/health` e `/api/v1/catalog/films`).

Também é possível rodar manualmente (Actions → Deploy produção → Run workflow),
com opção de pular migrations.

## 6. Migrations

- Arquivos: `db/migrations/001_…sql` a `012_performance.sql`, aplicados **em
  ordem alfabética**, cada um numa transação; para no primeiro erro.
- São **idempotentes** (podem ser reaplicadas sem perder dados) — toda
  migration nova precisa manter essa regra (`IF NOT EXISTS`, `ADD COLUMN IF NOT
  EXISTS`, `CREATE OR REPLACE`…).
- No deploy: passo **"Rodar migrations"** do `deploy.yml` executa uma task
  Fargate one-off com a **imagem nova** e a mesma rede/SG do serviço (o Aurora
  só aceita a SG da API). Se falhar, o serviço não é atualizado e a versão
  anterior continua no ar. A saída aparece no log do job e no log group
  `/ecs/videoteca-<env>-api`.
- Ordem é **migrar → trocar versão**: migrations precisam ser compatíveis com a
  versão anterior da API (só adicionar colunas/tabelas/índices; remoções num
  release posterior).
- Localmente (`docker compose`): o Postgres aplica 001–012 + seed só na
  primeira subida (volume vazio). Banco existente:

```powershell
Get-ChildItem db\migrations\*.sql | Sort-Object Name | ForEach-Object {
  Get-Content $_ -Raw | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -1 -U videoteca -d videoteca
}
```

## 7. Smoke test pós-deploy

```powershell
$api = "<API_BASE_URL>"
Invoke-RestMethod "$api/health"                                  # {"status":"ok"}
(Invoke-WebRequest "$api/api/v1/catalog/films").StatusCode      # 200
Invoke-WebRequest "$api/health" | Select-Object -ExpandProperty Headers  # X-Request-ID, headers de segurança
```

Depois, no front: login de aluno, professor e funcionário; catálogo; uma
reserva; publicação de sessão com filme autorizado. Conferir no dashboard
CloudWatch que não há 5xx e que a latência p95 está abaixo de 2 s.

## 8. Rollback

- **Automático**: o circuit breaker do ECS volta para a revisão anterior se as
  tasks novas não passarem no health check; o job falha.
- **Manual** (versão subiu saudável mas com bug): use a revisão anterior,
  registrada no resumo do job ("Task definition anterior").

```powershell
aws ecs update-service --cluster videoteca-prod-cluster --service videoteca-prod-api `
  --task-definition <arn-da-revisao-anterior>
aws ecs wait services-stable --cluster videoteca-prod-cluster --services videoteca-prod-api
```

  Ou rode o workflow manualmente a partir da tag anterior (sem migrations).
- **Banco**: migrations não têm "down". Para desfazer dados use o backup do
  Aurora (retenção 7 dias, restore point-in-time para um cluster novo e troca
  do segredo `database-url`). Por isso migrations só adicionam.
- O ECR guarda apenas as **5 imagens** mais recentes: rollback além disso exige
  rebuild da tag.

## 9. Terraform × deploy pelo CI

O workflow registra revisões novas da task definition fora do Terraform. Um
`terraform apply` posterior volta o serviço para a revisão do Terraform
(`api_image_tag`, `CORS_ORIGINS=*`). Antes de qualquer apply em produção:

- ajuste `api_image_tag` para a tag publicada (ex.: `v1.0.0`);
- rode o workflow de novo logo após o apply (reaplica `ENVIRONMENT` e `CORS`).

## 10. Desligar / destruir

```powershell
# Desliga o runtime pago (mantém fundação, ECR, budget, OIDC)
terraform apply -var-file=prod.tfvars -var enable_runtime=false

# Remove tudo (inclusive Aurora SEM snapshot final e segredos sem janela de recuperação)
terraform destroy -var-file=prod.tfvars
```

Antes do destroy: faça um dump se quiser guardar dados
(`pg_dump` via task one-off ou snapshot manual do cluster).

## 11. Observabilidade e alertas

| O quê | Onde |
|-------|------|
| Dashboard (latência p50/p95, 5xx, CPU/mem ECS, Aurora) | `terraform output cloudwatch_dashboard_url` |
| Logs por requisição (`request_id`, rota, status, duração) | log group `/ecs/videoteca-<env>-api` (14 dias) |
| Consultas prontas | Logs Insights → *Saved queries* `videoteca-<env>/api-*` |
| Alarmes | ALB 5xx, latência p95, hosts não saudáveis, CPU/mem ECS, CPU/conexões Aurora, 5xx nos logs → SNS `videoteca-<env>-alarms` |
| Custo | Budget `videoteca-<env>-mensal` (50/80/100% real, 100% previsto) + Cost Anomaly Detection (diário) |

## 12. Limitações conhecidas

- **HTTPS da API exige domínio.** O front no Amplify é HTTPS e o navegador
  bloqueia chamadas HTTP a partir dele (*mixed content*). Emita um certificado
  ACM para o domínio da API (ex.: `api.videoteca.<dominio>`), informe
  `api_certificate_arn` no tfvars e aponte o DNS (CNAME) para o `alb_dns_name`:
  o Terraform cria o listener 443 (TLS 1.3/1.2) e o 80 passa a redirecionar
  para HTTPS. Sem certificado o ALB fica só em HTTP (uso de desenvolvimento).
  Defina também `api_cors_origins` com a URL HTTPS do front.
- Tasks em subnet pública com IP público (sem NAT, por custo); a SG da API só
  aceita tráfego do ALB.
- Aurora com min 0 ACU: a primeira requisição após pausa pode levar ~15 s.
