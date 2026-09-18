output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "ecr_repository_url" {
  description = "URL para docker push da imagem da API"
  value       = aws_ecr_repository.api.repository_url
}

output "aurora_endpoint" {
  description = "Endpoint do Aurora (só quando enable_runtime = true)"
  value       = var.enable_runtime ? aws_rds_cluster.aurora[0].endpoint : "(desligado: enable_runtime=false)"
}

output "alb_url" {
  description = "URL pública da API (só quando enable_runtime = true)"
  value       = var.enable_runtime ? "http://${aws_lb.api[0].dns_name}" : "(desligado: enable_runtime=false)"
}
