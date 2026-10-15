mock_provider "aws" {}

variables {
  vpc_id              = "vpc-12345678"
  vpc_cidr            = "10.20.0.0/16"
  private_subnet_ids  = ["subnet-12345678", "subnet-87654321"]
  database_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:database-test"
  ses_identity_arn    = "arn:aws:ses:us-east-1:123456789012:identity/test.invalid"
  reports_image       = "123456789012.dkr.ecr.us-east-1.amazonaws.com/reports@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  email_image         = "123456789012.dkr.ecr.us-east-1.amazonaws.com/email@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  worker_image        = "123456789012.dkr.ecr.us-east-1.amazonaws.com/worker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  reports_command     = ["/app/reports"]
  email_command       = ["/app/email"]
  worker_command      = ["/app/sync"]
}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_batch_compute_environment.jobs) == 0 && length(aws_autoscaling_group.workers) == 0
    error_message = "Runtime precisa permanecer desabilitado por padrão."
  }
}

run "enabled_runtime" {
  command = plan
  variables { enabled = true }
  assert {
    condition     = aws_batch_compute_environment.jobs[0].compute_resources[0].max_vcpus == 4
    error_message = "Batch deve limitar o consumo de CPU."
  }
  assert {
    condition     = aws_batch_job_definition.reports[0].retry_strategy[0].attempts == 3 && aws_batch_job_definition.email[0].retry_strategy[0].attempts == 3
    error_message = "Jobs precisam de tentativas limitadas."
  }
  assert {
    condition     = aws_ecs_service.worker[0].deployment_circuit_breaker[0].rollback && aws_ecs_service.worker[0].deployment_minimum_healthy_percent == 100
    error_message = "Workers exigem rollback e rollout sem indisponibilidade."
  }
  assert {
    condition     = aws_launch_template.workers[0].metadata_options[0].http_tokens == "required" && aws_autoscaling_group.workers[0].max_size == 3
    error_message = "Nós exigem IMDSv2 e limite de escala."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.reports[0].block_public_policy && aws_sqs_queue.sync[0].sqs_managed_sse_enabled
    error_message = "Relatórios privados e fila criptografada são obrigatórios."
  }
}

run "reject_mutable_image" {
  command = plan
  variables { reports_image = "example/reports:latest" }
  expect_failures = [var.reports_image]
}
