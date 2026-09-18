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
