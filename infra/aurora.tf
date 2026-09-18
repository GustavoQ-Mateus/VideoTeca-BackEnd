# ─────────────────────────────────────────────────────────────────────────
# T26 — Banco de dados no Amazon Aurora (PostgreSQL, Serverless v2)
# Serverless v2 com min_capacity 0 (auto-pause): quando ocioso escala a 0 ACU
# e o custo de compute cai a ~zero, restando só storage. Writer + reader.
# Só é criado quando enable_runtime = true.
# ─────────────────────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "aurora" {
  count      = var.enable_runtime ? 1 : 0
  name       = "${local.name}-aurora-subnets"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_rds_cluster" "aurora" {
  count                   = var.enable_runtime ? 1 : 0
  cluster_identifier      = "${local.name}-aurora"
  engine                  = "aurora-postgresql"
  engine_mode             = "provisioned"
  engine_version          = "16.4"
  database_name           = "videoteca"
  master_username         = "vtadmin"
  master_password         = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.aurora[0].name
  vpc_security_group_ids  = [aws_security_group.db.id]
  backup_retention_period = 7 # T26.3 — retenção de backup
  storage_encrypted       = true
  skip_final_snapshot     = true
  apply_immediately       = true

  serverlessv2_scaling_configuration {
    min_capacity = 0 # escala a zero quando ocioso (custo ~zero)
    max_capacity = 1
  }
}

# T26.1 — writer
resource "aws_rds_cluster_instance" "writer" {
  count                = var.enable_runtime ? 1 : 0
  identifier           = "${local.name}-aurora-writer"
  cluster_identifier   = aws_rds_cluster.aurora[0].id
  instance_class       = "db.serverless"
  engine               = aws_rds_cluster.aurora[0].engine
  engine_version       = aws_rds_cluster.aurora[0].engine_version
  db_subnet_group_name = aws_db_subnet_group.aurora[0].name
}
