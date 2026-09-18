# ─────────────────────────────────────────────────────────────────────────
# T25.3 — Segredos e parâmetros (Secrets Manager + SSM)
# Senha do banco gerada pelo Terraform; JWT secret idem. A DATABASE_URL
# completa é montada e guardada como segredo, consumida pela task ECS.
# ─────────────────────────────────────────────────────────────────────────

resource "random_password" "db" {
  length  = 24
  special = false # evita caracteres problemáticos em connection string
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${local.name}/db-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db.result
}

resource "aws_secretsmanager_secret" "jwt" {
  name                    = "${local.name}/jwt-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id     = aws_secretsmanager_secret.jwt.id
  secret_string = random_password.jwt.result
}

# DATABASE_URL completa (só tem valor útil quando enable_runtime = true,
# pois depende do endpoint do Aurora).
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name}/database-url"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "database_url" {
  count     = var.enable_runtime ? 1 : 0
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql://%s:%s@%s:5432/%s",
    "vtadmin",
    random_password.db.result,
    aws_rds_cluster.aurora[0].endpoint,
    "videoteca",
  )
}

# Parâmetro não-sensível de exemplo no SSM (T25.3).
resource "aws_ssm_parameter" "cors_origins" {
  name  = "/${local.name}/cors-origins"
  type  = "String"
  value = "http://localhost:3000"
}
