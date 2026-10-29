# T24.3 — HTTPS da API no ALB (listener 443 + redirecionamento do 80).
# Provider simulado: nenhuma chamada à AWS.

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

run "sem_certificado_fica_http" {
  command = plan
  variables {
    enable_runtime = true
  }
  assert {
    condition     = length(aws_lb_listener.https) == 0
    error_message = "Sem certificado não deve haver listener 443"
  }
  assert {
    condition     = aws_lb_listener.http[0].default_action[0].type == "forward"
    error_message = "Sem certificado o 80 encaminha para a API"
  }
}

run "com_certificado_redireciona" {
  command = plan
  variables {
    enable_runtime      = true
    api_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
    api_cors_origins    = "https://videoteca.example.com"
  }
  assert {
    condition     = aws_lb_listener.https[0].port == 443 && aws_lb_listener.https[0].protocol == "HTTPS"
    error_message = "Listener 443 HTTPS esperado"
  }
  assert {
    condition     = aws_lb_listener.http[0].default_action[0].type == "redirect"
    error_message = "Com certificado o 80 redireciona para HTTPS"
  }
}

run "cors_curinga_recusado" {
  command = plan
  variables {
    api_cors_origins = "*"
  }
  expect_failures = [var.api_cors_origins]
}
