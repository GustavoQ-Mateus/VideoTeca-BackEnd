# T24.3 — role OIDC do GitHub Actions para deploy.
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

run "no_repository_no_role" {
  command = plan

  assert {
    condition     = length(aws_iam_role.github_deploy) == 0 && length(aws_iam_openid_connect_provider.github) == 0
    error_message = "Sem github_repository não cria OIDC nem role."
  }
}

run "role_without_runtime" {
  command = apply

  variables {
    github_repository = "GustavoQ-Mateus/VideoTeca-BackEnd"
  }

  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1 && aws_iam_openid_connect_provider.github[0].url == "https://token.actions.githubusercontent.com"
    error_message = "Provider OIDC do GitHub deve ser criado."
  }
  assert {
    condition     = local.gh_oidc_subject == "repo:GustavoQ-Mateus/VideoTeca-BackEnd:environment:production"
    error_message = "Trust deve aceitar só o environment production do repositório."
  }
  assert {
    condition     = local.gh_cluster_arn == "arn:aws:ecs:us-east-1:123456789012:cluster/videoteca-dev-cluster"
    error_message = "ARN do cluster deve seguir o nome de ecs.tf."
  }
  assert {
    condition     = output.github_deploy_settings.ECS_SERVICE == "videoteca-dev-api" && output.github_deploy_settings.ECS_CLUSTER == "videoteca-dev-cluster" && output.github_deploy_settings.ECS_TASK_FAMILY == "videoteca-dev-api"
    error_message = "Outputs devem bater com os nomes de ecs.tf."
  }
}

run "reuses_existing_provider" {
  command = plan

  variables {
    github_repository        = "GustavoQ-Mateus/VideoTeca-BackEnd"
    github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  }

  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0 && length(aws_iam_role.github_deploy) == 1
    error_message = "Com provider existente, só a role é criada."
  }
}

run "rejects_bad_repository" {
  command = plan

  variables {
    github_repository = "sem-barra"
  }

  expect_failures = [var.github_repository]
}
