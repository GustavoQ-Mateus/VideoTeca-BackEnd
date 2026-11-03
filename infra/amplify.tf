# ─────────────────────────────────────────────────────────────────────────
# T27 — Front no AWS Amplify (build a partir do GitHub, previews por branch)
# Desligado por padrão: exige token do GitHub e `developer` com todas as
# entregas integradas. Até lá a homologação usa o front em Fargate (edge.tf).
#   export TF_VAR_github_token=<fine-grained, read-only em VideoTeca-FrontEnd>
#   terraform apply -var enable_amplify=true ...
# ─────────────────────────────────────────────────────────────────────────

variable "enable_amplify" {
  description = "Cria o app Amplify ligado ao repositório do front"
  type        = bool
  default     = false
}

variable "github_token" {
  description = "Token do GitHub para o Amplify ler o repositório (nunca commitar; use TF_VAR_github_token)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "frontend_repository" {
  description = "URL do repositório do front"
  type        = string
  default     = "https://github.com/GustavoQ-Mateus/VideoTeca-FrontEnd"
}

variable "amplify_branch" {
  description = "Branch publicada pelo Amplify"
  type        = string
  default     = "developer"
}

resource "aws_amplify_app" "web" {
  count        = var.enable_amplify ? 1 : 0
  name         = "${local.name}-web"
  repository   = var.frontend_repository
  access_token = var.github_token
  platform     = "WEB_COMPUTE" # Next.js com SSR/middleware

  # T27.3 — preview automático por PR
  enable_auto_branch_creation = false
  enable_branch_auto_build    = true

  environment_variables = {
    # Com o edge ligado, o Amplify usa a API pelo CloudFront (HTTPS).
    NEXT_PUBLIC_API_URL = local.edge ? "https://${aws_cloudfront_distribution.edge[0].domain_name}" : ""
  }

  lifecycle {
    precondition {
      condition     = var.github_token != ""
      error_message = "Defina TF_VAR_github_token para criar o app Amplify."
    }
  }
}

resource "aws_amplify_branch" "main" {
  count                       = var.enable_amplify ? 1 : 0
  app_id                      = aws_amplify_app.web[0].id
  branch_name                 = var.amplify_branch
  stage                       = "BETA"
  enable_auto_build           = true
  enable_pull_request_preview = true
}

output "amplify_url" {
  value = var.enable_amplify ? "https://${var.amplify_branch}.${aws_amplify_app.web[0].default_domain}" : "(desligado: enable_amplify=false)"
}
