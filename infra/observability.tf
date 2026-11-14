# ─────────────────────────────────────────────────────────────────────────
# T31.1 — Logs e métricas no CloudWatch
#
# A API (app/core/observability.py) escreve no stdout, coletado pelo driver
# awslogs no log group aws_cloudwatch_log_group.api (ecs.tf, retenção 14 dias):
#   - 1 linha JSON por requisição: ts, level, request_id, method, path,
#     status, duration_ms (sem corpo, token, e-mail ou senha);
#   - linhas EMF (namespace Videoteca/API, métricas Latency/Errors5xx,
#     dimensão Route), extraídas automaticamente pelo CloudWatch.
#
# Aqui: metric filters (independentes do EMF, sem dimensão → custo fixo de
# 3 métricas), consultas salvas do Logs Insights e um dashboard com ALB,
# API, ECS e Aurora. Tudo só existe quando enable_runtime = true.
# ─────────────────────────────────────────────────────────────────────────

locals {
  # Namespace dos metric filters. Separado do namespace EMF (Videoteca/API)
  # para não misturar séries com e sem dimensão Route.
  obs_log_namespace = "Videoteca/API/Logs"
  obs_emf_namespace = "Videoteca/API"

  obs_metric_requests = "RequestCount"
  obs_metric_errors   = "Http5xxCount"
  obs_metric_latency  = "RequestLatencyMs"

  # Retenção esperada do log group da API (definida em ecs.tf). Verificada
  # pelo check abaixo e pelos testes para não deixar logs sem expiração.
  obs_max_log_retention_days = 30
}

# ── Metric filters ───────────────────────────────────────────────────────
# Só casam linhas de acesso (têm request_id e status numérico); linhas EMF
# e logs de outras origens são ignorados.

resource "aws_cloudwatch_log_metric_filter" "api_requests" {
  count          = var.enable_runtime ? 1 : 0
  name           = "${local.name}-api-requests"
  log_group_name = aws_cloudwatch_log_group.api[0].name
  pattern        = "{ ($.request_id = *) && ($.status >= 100) }"

  metric_transformation {
    name          = local.obs_metric_requests
    namespace     = local.obs_log_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "api_errors_5xx" {
  count          = var.enable_runtime ? 1 : 0
  name           = "${local.name}-api-errors-5xx"
  log_group_name = aws_cloudwatch_log_group.api[0].name
  pattern        = "{ ($.request_id = *) && ($.status >= 500) }"

  metric_transformation {
    name          = local.obs_metric_errors
    namespace     = local.obs_log_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "api_latency" {
  count          = var.enable_runtime ? 1 : 0
  name           = "${local.name}-api-latency"
  log_group_name = aws_cloudwatch_log_group.api[0].name
  pattern        = "{ ($.request_id = *) && ($.duration_ms >= 0) }"

  metric_transformation {
    name      = local.obs_metric_latency
    namespace = local.obs_log_namespace
    value     = "$.duration_ms"
    unit      = "Milliseconds"
  }
}

# ── Consultas salvas do Logs Insights (sem custo fixo) ────────────────────

resource "aws_cloudwatch_query_definition" "api_errors" {
  count           = var.enable_runtime ? 1 : 0
  name            = "${local.name}/api-erros-5xx"
  log_group_names = [aws_cloudwatch_log_group.api[0].name]
  query_string    = <<-EOT
    fields @timestamp, request_id, method, path, status, duration_ms
    | filter ispresent(request_id) and status >= 500
    | sort @timestamp desc
    | limit 100
  EOT
}

resource "aws_cloudwatch_query_definition" "api_slow_routes" {
  count           = var.enable_runtime ? 1 : 0
  name            = "${local.name}/api-rotas-lentas"
  log_group_names = [aws_cloudwatch_log_group.api[0].name]
  query_string    = <<-EOT
    filter ispresent(request_id)
    | stats count(*) as reqs, pct(duration_ms, 50) as p50, pct(duration_ms, 95) as p95, max(duration_ms) as max_ms by method, path
    | sort p95 desc
    | limit 25
  EOT
}

resource "aws_cloudwatch_query_definition" "api_request_trace" {
  count           = var.enable_runtime ? 1 : 0
  name            = "${local.name}/api-rastrear-request-id"
  log_group_names = [aws_cloudwatch_log_group.api[0].name]
  query_string    = <<-EOT
    fields @timestamp, @message
    | filter request_id = "COLE_O_X_REQUEST_ID_AQUI"
    | sort @timestamp asc
  EOT
}

# ── Dashboard ────────────────────────────────────────────────────────────

locals {
  obs_region = data.aws_region.current.name
}

resource "aws_cloudwatch_dashboard" "api" {
  count          = var.enable_runtime ? 1 : 0
  dashboard_name = "${local.name}-api"
  dashboard_body = jsonencode({ widgets = [
    {
      type = "text", x = 0, y = 0, width = 24, height = 2
      properties = {
        markdown = "## Videoteca — API (${var.environment})\nLatência e erros vêm do ALB e dos logs da API (metric filters). Aurora escala a 0 ACU quando ocioso: lacunas nos gráficos do banco são esperadas."
      }
    },
    {
      type = "metric", x = 0, y = 2, width = 12, height = 6
      properties = {
        title  = "Latência ALB → API (s): p50 / p95"
        region = local.obs_region
        view   = "timeSeries"
        period = 60
        metrics = [
          ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.api[0].arn_suffix, { stat = "p50", label = "p50" }],
          ["...", { stat = "p95", label = "p95" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 2, width = 12, height = 6
      properties = {
        title  = "Latência na aplicação (ms): p50 / p95"
        region = local.obs_region
        view   = "timeSeries"
        period = 60
        metrics = [
          [local.obs_log_namespace, local.obs_metric_latency, { stat = "p50", label = "p50" }],
          ["...", { stat = "p95", label = "p95" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 8, width = 12, height = 6
      properties = {
        title  = "Erros 5xx (soma por minuto)"
        region = local.obs_region
        view   = "timeSeries"
        stat   = "Sum"
        period = 60
        metrics = [
          ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.api[0].arn_suffix, { label = "ALB: 5xx da API" }],
          [".", "HTTPCode_ELB_5XX_Count", ".", ".", { label = "ALB: 5xx do próprio ALB" }],
          [local.obs_log_namespace, local.obs_metric_errors, { label = "Logs da API: status >= 500" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 8, width = 12, height = 6
      properties = {
        title  = "Requisições e hosts saudáveis"
        region = local.obs_region
        view   = "timeSeries"
        period = 60
        metrics = [
          ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.api[0].arn_suffix, { stat = "Sum", label = "Requisições (ALB)" }],
          [".", "HealthyHostCount", "TargetGroup", aws_lb_target_group.api[0].arn_suffix, "LoadBalancer", aws_lb.api[0].arn_suffix, { stat = "Minimum", label = "Saudáveis", yAxis = "right" }],
          [".", "UnHealthyHostCount", ".", ".", ".", ".", { stat = "Maximum", label = "Não saudáveis", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 14, width = 12, height = 6
      properties = {
        title  = "Latência p95 por rota (EMF, ms)"
        region = local.obs_region
        view   = "timeSeries"
        period = 300
        metrics = [
          [{ expression = "SEARCH('{${local.obs_emf_namespace},Route} MetricName=\"Latency\"', 'p95', 300)", id = "e1", label = "" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 14, width = 12, height = 6
      properties = {
        title  = "ECS Fargate: CPU e memória (%)"
        region = local.obs_region
        view   = "timeSeries"
        stat   = "Average"
        period = 60
        yAxis  = { left = { min = 0, max = 100 } }
        metrics = [
          ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main[0].name, "ServiceName", aws_ecs_service.api[0].name, { label = "CPU" }],
          [".", "MemoryUtilization", ".", ".", ".", ".", { label = "Memória" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 20, width = 12, height = 6
      properties = {
        title  = "Aurora: conexões"
        region = local.obs_region
        view   = "timeSeries"
        stat   = "Maximum"
        period = 60
        metrics = [
          ["AWS/RDS", "DatabaseConnections", "DBClusterIdentifier", aws_rds_cluster.aurora[0].cluster_identifier, { label = "Conexões" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 20, width = 12, height = 6
      properties = {
        title  = "Aurora: CPU (%) e capacidade (ACU)"
        region = local.obs_region
        view   = "timeSeries"
        stat   = "Average"
        period = 60
        metrics = [
          ["AWS/RDS", "CPUUtilization", "DBClusterIdentifier", aws_rds_cluster.aurora[0].cluster_identifier, { label = "CPU %" }],
          [".", "ServerlessDatabaseCapacity", ".", ".", { label = "ACU", yAxis = "right" }],
        ]
      }
    },
  ] })
}

# Garante que o log group da API nunca fique sem retenção (custo de storage).
check "api_log_retention" {
  assert {
    condition = !var.enable_runtime || try(
      aws_cloudwatch_log_group.api[0].retention_in_days > 0 &&
      aws_cloudwatch_log_group.api[0].retention_in_days <= local.obs_max_log_retention_days,
      false
    )
    error_message = "O log group da API precisa de retenção entre 1 e ${local.obs_max_log_retention_days} dias."
  }
}

output "cloudwatch_dashboard_url" {
  description = "Dashboard CloudWatch da API (só quando enable_runtime = true)"
  value       = var.enable_runtime ? "https://${local.obs_region}.console.aws.amazon.com/cloudwatch/home?region=${local.obs_region}#dashboards/dashboard/${aws_cloudwatch_dashboard.api[0].dashboard_name}" : "(desligado: enable_runtime=false)"
}
