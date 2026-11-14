# T31.1 — metric filters, consultas salvas e dashboard.
# Provider simulado: nenhuma chamada à AWS (seguro rodar `terraform test`).

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b"] }
  }
  mock_data "aws_region" {
    defaults = { name = "us-east-1" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  # ARNs válidos: o provider valida o formato mesmo com mock.
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_lb" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/mock/0123456789abcdef"
      arn_suffix = "app/mock/0123456789abcdef"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/mock/0123456789abcdef"
      arn_suffix = "targetgroup/mock/0123456789abcdef"
    }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:mock" }
  }
  mock_resource "aws_ce_anomaly_monitor" {
    defaults = { arn = "arn:aws:ce::123456789012:anomalymonitor/mock" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
}

run "runtime_off_has_no_observability" {
  command = plan

  assert {
    condition     = length(aws_cloudwatch_log_metric_filter.api_errors_5xx) == 0 && length(aws_cloudwatch_dashboard.api) == 0
    error_message = "Sem runtime não deve haver metric filters nem dashboard."
  }
  assert {
    condition     = output.cloudwatch_dashboard_url == "(desligado: enable_runtime=false)"
    error_message = "Output do dashboard deve indicar runtime desligado."
  }
}

run "runtime_on_creates_filters_and_dashboard" {
  command = apply

  variables {
    enable_runtime = true
  }

  assert {
    condition     = aws_cloudwatch_log_metric_filter.api_errors_5xx[0].log_group_name == aws_cloudwatch_log_group.api[0].name
    error_message = "Metric filter precisa usar o log group da API."
  }
  assert {
    condition     = strcontains(aws_cloudwatch_log_metric_filter.api_errors_5xx[0].pattern, "$.status >= 500")
    error_message = "Filtro de erros deve casar status >= 500."
  }
  assert {
    condition     = aws_cloudwatch_log_metric_filter.api_latency[0].metric_transformation[0].value == "$.duration_ms"
    error_message = "Filtro de latência deve publicar duration_ms."
  }
  assert {
    condition     = aws_cloudwatch_log_metric_filter.api_requests[0].metric_transformation[0].namespace == "Videoteca/API/Logs"
    error_message = "Metric filters ficam no namespace Videoteca/API/Logs."
  }
  assert {
    condition     = aws_cloudwatch_log_group.api[0].retention_in_days > 0 && aws_cloudwatch_log_group.api[0].retention_in_days <= 30
    error_message = "Log group da API precisa de retenção limitada."
  }
  assert {
    condition     = length(aws_cloudwatch_query_definition.api_errors) == 1 && length(aws_cloudwatch_query_definition.api_slow_routes) == 1
    error_message = "Consultas salvas do Logs Insights devem existir."
  }
  assert {
    condition = alltrue([
      for m in ["TargetResponseTime", "p95", "p50", "HTTPCode_Target_5XX_Count", "CPUUtilization", "MemoryUtilization", "DatabaseConnections", "Videoteca/API"] :
      strcontains(aws_cloudwatch_dashboard.api[0].dashboard_body, m)
    ])
    error_message = "Dashboard deve ter latência p50/p95, 5xx, CPU/memória ECS, conexões Aurora e métricas EMF."
  }
  assert {
    condition     = length(jsondecode(aws_cloudwatch_dashboard.api[0].dashboard_body).widgets) >= 8
    error_message = "Dashboard incompleto."
  }
}
