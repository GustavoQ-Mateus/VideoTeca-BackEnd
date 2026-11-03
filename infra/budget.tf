# ─────────────────────────────────────────────────────────────────────────
# T31.3 — Budget e alertas de custo
#
# Existe SEMPRE (independe de enable_runtime): AWS Budgets (2 primeiros
# budgets da conta são grátis) e Cost Anomaly Detection (grátis).
#
# Notificações: real 50/80/100% e previsto 100% do limite mensal.
# Sem e-mail configurado o budget é criado, mas sem notificações.
# ─────────────────────────────────────────────────────────────────────────

variable "budget_limit_usd" {
  description = "Limite mensal de custo (USD) do budget"
  type        = number
  default     = 20
  validation {
    condition     = var.budget_limit_usd > 0
    error_message = "budget_limit_usd deve ser maior que zero."
  }
}

variable "budget_alert_emails" {
  description = "E-mails que recebem alertas de budget e de anomalia de custo (máx. 10)"
  type        = list(string)
  default     = []
  validation {
    condition = length(var.budget_alert_emails) <= 10 && alltrue([
      for e in var.budget_alert_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))
    ])
    error_message = "budget_alert_emails aceita até 10 e-mails válidos."
  }
}

variable "budget_filter_by_project_tag" {
  description = "Filtra o budget pela tag projeto=videoteca.Exige ativar a tag 'projeto' como cost allocation tag no Billing; senão o budget fica zerado. false = custo da conta inteira."
  type        = bool
  default     = false
}

variable "enable_cost_anomaly_monitor" {
  description = "Cria monitor de anomalia por serviço. A AWS permite só UM monitor DIMENSIONAL/SERVICE por conta: use false se a conta já tiver um."
  type        = bool
  default     = true
}

variable "cost_anomaly_threshold_usd" {
  description = "Impacto absoluto mínimo (USD) para notificar uma anomalia de custo"
  type        = number
  default     = 5
  validation {
    condition     = var.cost_anomaly_threshold_usd > 0
    error_message = "cost_anomaly_threshold_usd deve ser maior que zero."
  }
}

locals {
  budget_notifications = [
    { type = "ACTUAL", threshold = 50 },
    { type = "ACTUAL", threshold = 80 },
    { type = "ACTUAL", threshold = 100 },
    { type = "FORECASTED", threshold = 100 },
  ]
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-mensal"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "cost_filter" {
    for_each = var.budget_filter_by_project_tag ? [1] : []
    content {
      name = "TagKeyValue"
      # Mesmo valor de default_tags.projeto em versions.tf.
      values = ["user:projeto$videoteca"]
    }
  }

  dynamic "notification" {
    for_each = length(var.budget_alert_emails) > 0 ? local.budget_notifications : []
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = var.budget_alert_emails
    }
  }
}

# ── Cost Anomaly Detection ───────────────────────────────────────────────

resource "aws_ce_anomaly_monitor" "services" {
  count             = var.enable_cost_anomaly_monitor ? 1 : 0
  name              = "${local.name}-servicos"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

# E-mail só aceita frequência DAILY/WEEKLY (IMMEDIATE exige SNS).
resource "aws_ce_anomaly_subscription" "email" {
  count            = var.enable_cost_anomaly_monitor && length(var.budget_alert_emails) > 0 ? 1 : 0
  name             = "${local.name}-anomalias"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services[0].arn]

  dynamic "subscriber" {
    for_each = var.budget_alert_emails
    content {
      type    = "EMAIL"
      address = subscriber.value
    }
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.cost_anomaly_threshold_usd)]
    }
  }
}

output "budget_name" {
  description = "Nome do budget mensal (Billing → Budgets)"
  value       = aws_budgets_budget.monthly.name
}
