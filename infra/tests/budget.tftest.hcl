# T31.3 — budget mensal e Cost Anomaly Detection.
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

run "budget_exists_without_runtime" {
  command = plan

  assert {
    condition     = aws_budgets_budget.monthly.limit_amount == "20.00" && aws_budgets_budget.monthly.time_unit == "MONTHLY" && aws_budgets_budget.monthly.limit_unit == "USD"
    error_message = "Budget mensal de 20 USD deve existir por padrão, mesmo sem runtime."
  }
  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 0 && length(aws_ce_anomaly_subscription.email) == 0
    error_message = "Sem e-mail não há notificações."
  }
  assert {
    condition     = length(aws_ce_anomaly_monitor.services) == 1 && length(aws_budgets_budget.monthly.cost_filter) == 0
    error_message = "Monitor de anomalia criado; budget cobre a conta inteira por padrão."
  }
}

run "budget_with_emails_and_runtime" {
  command = apply

  variables {
    enable_runtime               = true
    budget_limit_usd             = 35
    budget_alert_emails          = ["ops@example.com"]
    budget_filter_by_project_tag = true
  }

  assert {
    condition     = aws_budgets_budget.monthly.limit_amount == "35.00"
    error_message = "Limite deve vir da variável."
  }
  assert {
    condition = (
      length(aws_budgets_budget.monthly.notification) == 4 &&
      length([for n in aws_budgets_budget.monthly.notification : n if n.notification_type == "FORECASTED" && n.threshold == 100]) == 1 &&
      toset([for n in aws_budgets_budget.monthly.notification : n.threshold if n.notification_type == "ACTUAL"]) == toset([50, 80, 100])
    )
    error_message = "Notificações devem ser 50/80/100% real e 100% previsto."
  }
  assert {
    condition     = alltrue([for n in aws_budgets_budget.monthly.notification : contains(n.subscriber_email_addresses, "ops@example.com")])
    error_message = "Notificações devem ir para os e-mails configurados."
  }
  assert {
    condition     = one(aws_budgets_budget.monthly.cost_filter).values == tolist(["user:projeto$videoteca"])
    error_message = "Filtro por tag de projeto deve usar a cost allocation tag projeto."
  }
  assert {
    condition     = aws_ce_anomaly_subscription.email[0].frequency == "DAILY" && one(aws_ce_anomaly_subscription.email[0].subscriber).address == "ops@example.com"
    error_message = "Anomalias devem ser enviadas por e-mail diariamente."
  }
}

run "anomaly_monitor_can_be_disabled" {
  command = plan

  variables {
    enable_cost_anomaly_monitor = false
    budget_alert_emails         = ["ops@example.com"]
  }

  assert {
    condition     = length(aws_ce_anomaly_monitor.services) == 0 && length(aws_ce_anomaly_subscription.email) == 0
    error_message = "Conta que já tem monitor por serviço deve poder desligar o nosso."
  }
}

run "rejects_invalid_budget" {
  command = plan

  variables {
    budget_limit_usd = 0
  }

  expect_failures = [var.budget_limit_usd]
}
