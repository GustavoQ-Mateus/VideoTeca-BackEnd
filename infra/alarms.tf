# ─────────────────────────────────────────────────────────────────────────
# T31.2 — Alarmes de erro e latência
#
# Tópico SNS (+ assinatura por e-mail opcional) e alarmes para ALB, ECS,
# Aurora e para o metric filter de erros da aplicação (observability.tf).
# Tudo só existe quando enable_runtime = true (mesmo flag do runtime pago).
#
# A assinatura por e-mail precisa ser CONFIRMADA no link que a AWS envia;
# até lá o SNS não entrega nada.
# ─────────────────────────────────────────────────────────────────────────

variable "alarm_email" {
  description = "E-mail que recebe os alarmes (vazio = sem assinatura; o tópico SNS é criado mesmo assim)"
  type        = string
  default     = ""
  validation {
    condition     = var.alarm_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alarm_email))
    error_message = "alarm_email deve ser um e-mail válido ou vazio."
  }
}

variable "alarm_5xx_threshold" {
  description = "Quantidade de respostas 5xx em 5 minutos que dispara alarme"
  type        = number
  default     = 5
  validation {
    condition     = var.alarm_5xx_threshold >= 1
    error_message = "alarm_5xx_threshold deve ser >= 1."
  }
}

variable "alarm_latency_p95_seconds" {
  description = "Latência p95 (segundos) do ALB até a API que dispara alarme"
  type        = number
  default     = 2
  validation {
    condition     = var.alarm_latency_p95_seconds > 0 && var.alarm_latency_p95_seconds < 60
    error_message = "alarm_latency_p95_seconds deve estar entre 0 e 60 (requisições são cortadas em 55 s)."
  }
}

variable "alarm_ecs_utilization_percent" {
  description = "CPU/memória média (%) do serviço ECS que dispara alarme"
  type        = number
  default     = 85
  validation {
    condition     = var.alarm_ecs_utilization_percent > 0 && var.alarm_ecs_utilization_percent <= 100
    error_message = "alarm_ecs_utilization_percent deve estar entre 1 e 100."
  }
}

variable "alarm_db_cpu_percent" {
  description = "CPU (%) do Aurora que dispara alarme"
  type        = number
  default     = 80
}

variable "alarm_db_connections" {
  description = "Número de conexões do Aurora que dispara alarme (1 ACU suporta ~189)"
  type        = number
  default     = 80
}

locals {
  alarm_actions = var.enable_runtime ? [aws_sns_topic.alarms[0].arn] : []
}

resource "aws_sns_topic" "alarms" {
  count = var.enable_runtime ? 1 : 0
  name  = "${local.name}-alarms"
  # Sem KMS de propósito: a chave gerenciada alias/aws/sns não permite que o
  # CloudWatch publique; uma CMK própria custaria US$1/mês. Mensagens de
  # alarme não contêm dados pessoais.
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.enable_runtime && var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ── ALB ──────────────────────────────────────────────────────────────────

# 5xx gerados pela API + pelo próprio ALB (ex.: 502/503 sem target saudável).
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  count               = var.enable_runtime ? 1 : 0
  alarm_name          = "${local.name}-alb-5xx"
  alarm_description   = "Respostas 5xx (API + ALB) >= ${var.alarm_5xx_threshold} em 5 min"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = var.alarm_5xx_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "total"
    expression  = "FILL(target, 0) + FILL(elb, 0)"
    label       = "5xx total"
    return_data = true
  }

  metric_query {
    id = "target"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      dimensions  = { LoadBalancer = aws_lb.api[0].arn_suffix }
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id = "elb"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      dimensions  = { LoadBalancer = aws_lb.api[0].arn_suffix }
      period      = 300
      stat        = "Sum"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_latency_p95" {
  count               = var.enable_runtime ? 1 : 0
  alarm_name          = "${local.name}-alb-latency-p95"
  alarm_description   = "Latência p95 da API > ${var.alarm_latency_p95_seconds}s em 2 de 3 janelas de 5 min"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = aws_lb.api[0].arn_suffix }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.alarm_latency_p95_seconds
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_hosts" {
  count             = var.enable_runtime ? 1 : 0
  alarm_name        = "${local.name}-alb-unhealthy-hosts"
  alarm_description = "Há task da API falhando no health check (/health) do ALB"
  namespace         = "AWS/ApplicationELB"
  metric_name       = "UnHealthyHostCount"
  dimensions = {
    LoadBalancer = aws_lb.api[0].arn_suffix
    TargetGroup  = aws_lb_target_group.api[0].arn_suffix
  }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ── ECS ──────────────────────────────────────────────────────────────────
# AWS/ECS CPUUtilization/MemoryUtilization por serviço existem mesmo com
# Container Insights desligado (ecs.tf).

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  count             = var.enable_runtime ? 1 : 0
  alarm_name        = "${local.name}-ecs-cpu-high"
  alarm_description = "CPU média do serviço da API > ${var.alarm_ecs_utilization_percent}% por 10 min (autoscaling já no teto?)"
  namespace         = "AWS/ECS"
  metric_name       = "CPUUtilization"
  dimensions = {
    ClusterName = aws_ecs_cluster.main[0].name
    ServiceName = aws_ecs_service.api[0].name
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.alarm_ecs_utilization_percent
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "ecs_memory_high" {
  count             = var.enable_runtime ? 1 : 0
  alarm_name        = "${local.name}-ecs-memory-high"
  alarm_description = "Memória média do serviço da API > ${var.alarm_ecs_utilization_percent}% por 10 min"
  namespace         = "AWS/ECS"
  metric_name       = "MemoryUtilization"
  dimensions = {
    ClusterName = aws_ecs_cluster.main[0].name
    ServiceName = aws_ecs_service.api[0].name
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.alarm_ecs_utilization_percent
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ── Aurora ───────────────────────────────────────────────────────────────
# Com min_capacity = 0 o cluster pausa e para de publicar métricas:
# treat_missing_data = notBreaching evita alarme falso nesse período.

resource "aws_cloudwatch_metric_alarm" "aurora_cpu_high" {
  count               = var.enable_runtime ? 1 : 0
  alarm_name          = "${local.name}-aurora-cpu-high"
  alarm_description   = "CPU do Aurora > ${var.alarm_db_cpu_percent}% por 15 min"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBClusterIdentifier = aws_rds_cluster.aurora[0].cluster_identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.alarm_db_cpu_percent
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "aurora_connections_high" {
  count               = var.enable_runtime ? 1 : 0
  alarm_name          = "${local.name}-aurora-connections-high"
  alarm_description   = "Conexões no Aurora > ${var.alarm_db_connections} (pool vazando ou escala excessiva)"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBClusterIdentifier = aws_rds_cluster.aurora[0].cluster_identifier }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.alarm_db_connections
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ── Aplicação (metric filter de observability.tf) ────────────────────────

resource "aws_cloudwatch_metric_alarm" "app_errors_5xx" {
  count               = var.enable_runtime ? 1 : 0
  alarm_name          = "${local.name}-app-errors-5xx"
  alarm_description   = "Logs da API registraram >= ${var.alarm_5xx_threshold} respostas 5xx em 5 min (consulte a query salva api-erros-5xx)"
  namespace           = local.obs_log_namespace
  metric_name         = local.obs_metric_errors
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.alarm_5xx_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  depends_on = [aws_cloudwatch_log_metric_filter.api_errors_5xx]
}

output "alarms_sns_topic_arn" {
  description = "Tópico SNS dos alarmes (só quando enable_runtime = true)"
  value       = var.enable_runtime ? aws_sns_topic.alarms[0].arn : "(desligado: enable_runtime=false)"
}
