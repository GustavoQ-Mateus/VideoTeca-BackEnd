# T31.2 — SNS + alarmes de erro e latência.
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

run "runtime_off_has_no_alarms" {
  command = plan

  assert {
    condition     = length(aws_sns_topic.alarms) == 0 && length(aws_cloudwatch_metric_alarm.alb_5xx) == 0 && length(aws_cloudwatch_metric_alarm.app_errors_5xx) == 0
    error_message = "Sem runtime não deve haver SNS nem alarmes."
  }
}

run "runtime_on_without_email" {
  command = apply

  variables {
    enable_runtime = true
  }

  assert {
    condition     = length(aws_sns_topic.alarms) == 1 && length(aws_sns_topic_subscription.alarms_email) == 0
    error_message = "Sem e-mail: tópico criado, sem assinatura."
  }
}

run "runtime_on_with_email" {
  command = apply

  variables {
    enable_runtime = true
    alarm_email    = "ops@example.com"
  }

  assert {
    condition     = aws_sns_topic_subscription.alarms_email[0].protocol == "email" && aws_sns_topic_subscription.alarms_email[0].endpoint == "ops@example.com"
    error_message = "Assinatura por e-mail deve ser criada."
  }
  assert {
    condition = alltrue([
      for a in [
        aws_cloudwatch_metric_alarm.alb_5xx[0],
        aws_cloudwatch_metric_alarm.alb_latency_p95[0],
        aws_cloudwatch_metric_alarm.alb_unhealthy_hosts[0],
        aws_cloudwatch_metric_alarm.ecs_cpu_high[0],
        aws_cloudwatch_metric_alarm.ecs_memory_high[0],
        aws_cloudwatch_metric_alarm.aurora_cpu_high[0],
        aws_cloudwatch_metric_alarm.aurora_connections_high[0],
        aws_cloudwatch_metric_alarm.app_errors_5xx[0],
      ] : contains(a.alarm_actions, aws_sns_topic.alarms[0].arn) && a.treat_missing_data == "notBreaching"
    ])
    error_message = "Todos os alarmes devem notificar o SNS e tolerar ausência de dados (Aurora pausado)."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.alb_latency_p95[0].extended_statistic == "p95" && aws_cloudwatch_metric_alarm.alb_latency_p95[0].metric_name == "TargetResponseTime"
    error_message = "Alarme de latência deve usar p95 do TargetResponseTime."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.alb_unhealthy_hosts[0].dimensions.TargetGroup == aws_lb_target_group.api[0].arn_suffix
    error_message = "UnHealthyHostCount deve olhar o target group da API."
  }
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.alb_5xx[0].metric_query) == 3
    error_message = "Alarme 5xx deve somar 5xx da API e do ALB."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.app_errors_5xx[0].namespace == aws_cloudwatch_log_metric_filter.api_errors_5xx[0].metric_transformation[0].namespace &&
      aws_cloudwatch_metric_alarm.app_errors_5xx[0].metric_name == aws_cloudwatch_log_metric_filter.api_errors_5xx[0].metric_transformation[0].name
    )
    error_message = "Alarme da aplicação deve usar a métrica do metric filter de erros."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.ecs_cpu_high[0].dimensions.ServiceName == aws_ecs_service.api[0].name
    error_message = "Alarme de CPU deve olhar o serviço da API."
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.aurora_connections_high[0].dimensions.DBClusterIdentifier == aws_rds_cluster.aurora[0].cluster_identifier
    error_message = "Alarme de conexões deve olhar o cluster Aurora."
  }
}

run "rejects_invalid_email" {
  command = plan

  variables {
    alarm_email = "nao-e-email"
  }

  expect_failures = [var.alarm_email]
}
