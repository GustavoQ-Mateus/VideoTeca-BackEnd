variable "region" {
  description = "Região AWS"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Profile da AWS CLI (conta pessoal, nunca a da empresa)"
  type        = string
  default     = "prdal-gustavo"
}

variable "environment" {
  description = "Ambiente (dev|prod) — T25.1"
  type        = string
  default     = "dev"
}

variable "project" {
  description = "Prefixo de nomes"
  type        = string
  default     = "videoteca"
}

variable "vpc_cidr" {
  description = "CIDR da VPC"
  type        = string
  default     = "10.20.0.0/16"
}

# ── Flag de custo ────────────────────────────────────────────────────────
# false (padrão): sobe só a fundação GRÁTIS (VPC, subnets, SG, IAM, SSM, ECR).
# true:  sobe também Aurora + ECS Fargate + ALB (recursos que COBRAM por hora).
# Ligue conscientemente e rode `terraform destroy` para não acumular custo.
variable "enable_runtime" {
  description = "Provisiona Aurora + Fargate + ALB (recursos pagos)"
  type        = bool
  default     = false
}

variable "api_image_tag" {
  description = "Tag da imagem da API no ECR"
  type        = string
  default     = "latest"
}

variable "api_desired_count" {
  description = "Nº de tasks Fargate desejadas"
  type        = number
  default     = 1
}

# ── T24.3 Publicação ─────────────────────────────────────────────────────
variable "api_certificate_arn" {
  description = "ARN do certificado ACM do domínio da API. Vazio = só HTTP (dev); produção exige HTTPS"
  type        = string
  default     = ""
}

variable "api_cors_origins" {
  description = "Origens permitidas no CORS da API, separadas por vírgula (nunca '*' em produção)"
  type        = string
  default     = "http://localhost:3000"

  validation {
    condition     = !strcontains(var.api_cors_origins, "*")
    error_message = "Use origens explícitas; '*' é recusado pela API em produção."
  }
}

locals {
  api_https = var.api_certificate_arn != ""
}
