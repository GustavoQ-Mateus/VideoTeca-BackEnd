# ─────────────────────────────────────────────────────────────────────────
# T24.3 — Deploy via GitHub Actions sem chaves estáticas (OIDC)
#
# Cria (quando github_repository != ""):
#   - o identity provider OIDC do GitHub (ou reaproveita um existente);
#   - a role assumida pelo workflow .github/workflows/deploy.yml, aceita
#     SOMENTE para jobs do repositório no environment GitHub "production"
#     (que exige aprovação manual);
#   - permissões mínimas: push no ECR da API, registrar task definition,
#     rodar a task de migração, atualizar o serviço, PassRole das 2 roles.
#
# Independe de enable_runtime (IAM não cobra). As ARNs de ECS são montadas
# pelos nomes de ecs.tf, então a role já pode existir antes do runtime.
# ─────────────────────────────────────────────────────────────────────────

variable "github_repository" {
  description = "Repositório GitHub autorizado a fazer deploy (owner/nome). Vazio = não cria OIDC/role."
  type        = string
  default     = ""
  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository deve ter o formato owner/repo."
  }
}

variable "github_environment" {
  description = "Environment do GitHub Actions autorizado a assumir a role (com aprovação manual)"
  type        = string
  default     = "production"
}

variable "github_oidc_provider_arn" {
  description = "ARN de um provider OIDC token.actions.githubusercontent.com já existente na conta (só pode haver um). Vazio = cria."
  type        = string
  default     = ""
}

locals {
  gh_enabled         = var.github_repository != ""
  gh_create_provider = local.gh_enabled && var.github_oidc_provider_arn == ""
  gh_provider_arn    = local.gh_create_provider ? aws_iam_openid_connect_provider.github[0].arn : var.github_oidc_provider_arn

  # Nomes definidos em ecs.tf (mantidos em sincronia).
  gh_account       = data.aws_caller_identity.current.account_id
  gh_region        = data.aws_region.current.name
  gh_cluster_name  = "${local.name}-cluster"
  gh_service_name  = "${local.name}-api"
  gh_task_family   = "${local.name}-api"
  gh_log_group     = "/ecs/${local.name}-api"
  gh_ecs_arn_base  = "arn:aws:ecs:${local.gh_region}:${local.gh_account}"
  gh_cluster_arn   = "${local.gh_ecs_arn_base}:cluster/${local.gh_cluster_name}"
  gh_oidc_subject  = "repo:${var.github_repository}:environment:${var.github_environment}"
  gh_log_group_arn = "arn:aws:logs:${local.gh_region}:${local.gh_account}:log-group:${local.gh_log_group}"
}

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.gh_create_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # A AWS valida o GitHub pela CA confiável; os thumbprints ficam por
  # compatibilidade com versões do provider que ainda exigem o campo.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "github_assume" {
  count = local.gh_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.gh_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Só jobs no environment protegido (aprovação manual) — nem branches,
    # nem PRs, nem forks conseguem assumir a role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.gh_oidc_subject]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = local.gh_enabled ? 1 : 0
  name                 = "${local.name}-github-deploy"
  description          = "Deploy da API pelo GitHub Actions (${var.github_repository}, env ${var.github_environment})"
  assume_role_policy   = data.aws_iam_policy_document.github_assume[0].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  count = local.gh_enabled ? 1 : 0

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # ação sem suporte a resource-level
  }

  statement {
    sid = "EcrPushApi"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  statement {
    sid = "EcsTaskDefinitions"
    # Register/Describe de task definition não suportam resource-level.
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  statement {
    sid       = "EcsDeployService"
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = ["${local.gh_ecs_arn_base}:service/${local.gh_cluster_name}/${local.gh_service_name}"]
  }

  statement {
    sid       = "EcsRunMigrationTask"
    actions   = ["ecs:RunTask"]
    resources = ["${local.gh_ecs_arn_base}:task-definition/${local.gh_task_family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.gh_cluster_arn]
    }
  }

  statement {
    sid       = "EcsWatchMigrationTask"
    actions   = ["ecs:DescribeTasks", "ecs:StopTask"]
    resources = ["${local.gh_ecs_arn_base}:task/${local.gh_cluster_name}/*"]
  }

  statement {
    sid       = "PassEcsRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadMigrationLogs"
    actions   = ["logs:GetLogEvents"]
    resources = ["${local.gh_log_group_arn}:log-stream:*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = local.gh_enabled ? 1 : 0
  name   = "${local.name}-github-deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}

# Valores para cadastrar como "variables" do environment production no GitHub.
output "github_deploy_settings" {
  description = "Variáveis do environment GitHub 'production' usadas por deploy.yml"
  value = local.gh_enabled ? {
    AWS_REGION      = local.gh_region
    AWS_ROLE_ARN    = aws_iam_role.github_deploy[0].arn
    ECR_REPOSITORY  = aws_ecr_repository.api.name
    ECS_CLUSTER     = local.gh_cluster_name
    ECS_SERVICE     = local.gh_service_name
    ECS_TASK_FAMILY = local.gh_task_family
  } : null
}
