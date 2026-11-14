# Infra AWS — Videoteca (Terraform)

Infraestrutura das tarefas **T25** (fundação), **T26** (Aurora) e **T28** (API em Fargate).

> Conta pessoal `765656213653`, profile **`prdal-gustavo`**. Nunca use o profile `default` (conta da empresa).

## Pré-requisitos

```powershell
$env:AWS_PROFILE="prdal-gustavo"
terraform init
```

## Modelo de custo — importante

O provisionamento é dividido por um flag `enable_runtime`:

| enable_runtime | O que sobe | Custo aprox. |
|----------------|-----------|--------------|
| `false` (padrão) | VPC, subnets, SGs, IAM, Secrets, ECR | ~US$1,20/mês (3 secrets) |
| `true` | + Aurora Serverless v2, ECS Fargate, ALB | ALB ~US$16/mês + Fargate ~US$9/mês + Aurora (≈0 ocioso, escala a 0 ACU) |

A fundação grátis **já está aplicada**. O runtime pago fica desligado por padrão.

⚠️ Reciclar a access key **não derruba** recurso já criado — ele cobra até um `terraform destroy`.

## Subir a Sprint 2 (Aurora + Fargate) — quando quiser

```powershell
$env:AWS_PROFILE="prdal-gustavo"
terraform apply -var enable_runtime=true
```

Aurora leva ~10 min. Ao final, `terraform output alb_url` dá a URL pública da API.
A imagem da API já está no ECR (`videoteca/api:latest`); para atualizar:

```powershell
aws ecr get-login-password --region us-east-1 --profile prdal-gustavo | docker login --username AWS --password-stdin 765656213653.dkr.ecr.us-east-1.amazonaws.com
docker build --platform linux/amd64 -t 765656213653.dkr.ecr.us-east-1.amazonaws.com/videoteca/api:latest ..
docker push 765656213653.dkr.ecr.us-east-1.amazonaws.com/videoteca/api:latest
aws ecs update-service --cluster videoteca-dev-cluster --service videoteca-dev-api --force-new-deployment --region us-east-1 --profile prdal-gustavo
```

## Atualizar hml depois de mudanças

A homologação (`hml.tfvars`) é CloudFront → ALB → Fargate (API + front Next.js) → Aurora.
Front e API ficam na **mesma origem** (URL do CloudFront, `terraform output app_url`);
o front é buildado com `NEXT_PUBLIC_API_URL=<app_url>` (entra no bundle no build).

Atalho (script na pasta `infra`, faz checagem da conta 765656213653 antes de tudo):

```powershell
cd VideoTeca-BackEnd\infra
.\hml-atualizar.ps1              # API + front + migrate/seed
.\hml-atualizar.ps1 -Api         # só API
.\hml-atualizar.ps1 -Web         # só front
.\hml-atualizar.ps1 -Migrate     # só migrations + seeds
```

Passo a passo manual equivalente:

```powershell
$env:AWS_PROFILE = "prdal-gustavo"
aws sts get-caller-identity --profile prdal-gustavo   # tem que ser 765656213653
$reg = "765656213653.dkr.ecr.us-east-1.amazonaws.com"
cmd /c "aws ecr get-login-password --region us-east-1 --profile prdal-gustavo | docker login --username AWS --password-stdin $reg"

# API (na pasta VideoTeca-BackEnd)
docker build --platform linux/amd64 -t "$reg/videoteca/api:hml" .
docker push "$reg/videoteca/api:hml"
aws ecs update-service --cluster videoteca-dev-cluster --service videoteca-dev-api --force-new-deployment --region us-east-1 --profile prdal-gustavo

# Front (na pasta VideoTeca-FrontEnd); app_url vem do terraform output na pasta infra
$app = terraform -chdir=..\VideoTeca-BackEnd\infra output -raw app_url
docker build --platform linux/amd64 --build-arg "NEXT_PUBLIC_API_URL=$app" -t "$reg/videoteca/web:hml" .
docker push "$reg/videoteca/web:hml"
aws ecs update-service --cluster videoteca-dev-cluster --service videoteca-dev-web --force-new-deployment --region us-east-1 --profile prdal-gustavo

# Migrations + seeds (task one-off com a task definition da API; seeds são idempotentes)
$net = aws ecs describe-services --cluster videoteca-dev-cluster --services videoteca-dev-api --region us-east-1 --query "services[0].networkConfiguration" --output json
[IO.File]::WriteAllText("$env:TEMP\net.json", ($net -join "`n"))
[IO.File]::WriteAllText("$env:TEMP\ovr.json", '{"containerOverrides":[{"name":"api","command":["python","-m","app.migrate","--seed"]}]}')
$task = aws ecs run-task --cluster videoteca-dev-cluster --launch-type FARGATE --task-definition videoteca-dev-api --network-configuration "file://$env:TEMP\net.json" --overrides "file://$env:TEMP\ovr.json" --region us-east-1 --query "tasks[0].taskArn" --output text
aws ecs wait tasks-stopped --cluster videoteca-dev-cluster --tasks $task --region us-east-1
aws logs get-log-events --log-group-name /ecs/videoteca-dev-api --log-stream-name "api/api/$($task.Split('/')[-1])" --start-from-head --region us-east-1 --query "events[].message" --output text
```

Observações:
- O migrate precisa da imagem da API **já com** as migrations/seeds novas (rebuild+push antes).
- No PowerShell 5.1, `aws ecr get-login-password | docker login` direto dá *400 Bad Request*; use o `cmd /c` acima.
- No PowerShell, passe `'-var-file=hml.tfvars'` entre aspas para o Terraform.
- Desligar tudo (para de cobrar): `$env:AWS_PROFILE="prdal-gustavo"; terraform destroy '-var-file=hml.tfvars'`.

## Desligar o runtime pago (mantendo a fundação)

```powershell
terraform apply -var enable_runtime=false
```

## Destruir tudo

```powershell
terraform destroy
```

## Mapa das tarefas

| Arquivo | Tarefas |
|---------|---------|
| `network.tf` | T25.2 (VPC, subnets, SGs) |
| `iam.tf` | T25.1 / T25.4 (roles, permissões de deploy) |
| `secrets.tf` | T25.3 (Secrets Manager / SSM) |
| `ecr.tf` | T28.1 (repositório de imagem) |
| `aurora.tf` | T26 (Aurora Serverless v2, writer, backup) |
| `ecs.tf` | T28.2–T28.5 (task def, serviço, ALB, autoscaling, rollback) |
