# ─────────────────────────────────────────────────────────────────────────
# Homologação na AWS sem domínio próprio (T01.5 / T24.3)
#   CloudFront (HTTPS *.cloudfront.net) → ALB (HTTP, só aceita o CloudFront)
#     /api/*, /health, /docs, /openapi.json → API (Fargate)
#     demais caminhos                      → front Next.js (Fargate)
# Mesma origem para front e API: sem mixed content e sem CORS entre domínios.
# Liga com enable_runtime = true e enable_edge = true.
# ─────────────────────────────────────────────────────────────────────────

variable "enable_edge" {
  description = "CloudFront + front Next.js em Fargate atrás do mesmo ALB (homologação sem domínio)"
  type        = bool
  default     = false
}

variable "web_image_tag" {
  description = "Tag da imagem do front no ECR"
  type        = string
  default     = "latest"
}

variable "web_desired_count" {
  description = "Nº de tasks do front"
  type        = number
  default     = 1
}

locals {
  edge = var.enable_runtime && var.enable_edge
  # Caminhos servidos pela API; o resto vai para o front.
  # Limite do ALB: 5 valores por regra (1 header + 4 caminhos).
  api_paths = ["/api/*", "/health", "/docs*", "/openapi.json"]
}

# Segredo compartilhado CloudFront → ALB: sem ele o ALB responde 403,
# então ninguém fala direto com o ALB contornando o HTTPS.
resource "random_password" "origin_verify" {
  length  = 40
  special = false
}

resource "aws_ecr_repository" "web" {
  name                 = "${var.project}/web"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "web" {
  repository = aws_ecr_repository.web.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Manter apenas as 5 imagens mais recentes"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_security_group" "web" {
  name        = "${local.name}-web-sg"
  description = "Front Next.js: so recebe do ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Next.js do ALB"
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-web-sg" }
}

resource "aws_cloudwatch_log_group" "web" {
  count             = local.edge ? 1 : 0
  name              = "/ecs/${local.name}-web"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "web" {
  count                    = local.edge ? 1 : 0
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name         = "web"
    image        = "${aws_ecr_repository.web.repository_url}:${var.web_image_tag}"
    essential    = true
    portMappings = [{ containerPort = 3000, protocol = "tcp" }]
    environment  = [{ name = "NODE_ENV", value = "production" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.web[0].name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "web"
      }
    }
  }])
}

resource "aws_lb_target_group" "web" {
  count       = local.edge ? 1 : 0
  name        = "${local.name}-web-tg"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/login"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener_rule" "api" {
  count        = local.edge ? 1 : 0
  listener_arn = aws_lb_listener.http[0].arn
  priority     = 10

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }
  condition {
    path_pattern { values = local.api_paths }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

resource "aws_lb_listener_rule" "web" {
  count        = local.edge ? 1 : 0
  listener_arn = aws_lb_listener.http[0].arn
  priority     = 20

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web[0].arn
  }
}

resource "aws_ecs_service" "web" {
  count           = local.edge ? 1 : 0
  name            = "${local.name}-web"
  cluster         = aws_ecs_cluster.main[0].id
  task_definition = aws_ecs_task_definition.web[0].arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = true # sem NAT, por custo
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web[0].arn
    container_name   = "web"
    container_port   = 3000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener_rule.web]
}

# Políticas gerenciadas da AWS (IDs fixos e públicos).
locals {
  cf_caching_disabled       = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  cf_caching_optimized      = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  cf_all_viewer_except_host = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
}

resource "aws_cloudfront_distribution" "edge" {
  count           = local.edge ? 1 : 0
  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name} — front + API (homologação)"
  price_class     = "PriceClass_All" # inclui edge em São Paulo/Fortaleza

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.api[0].dns_name

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # ALB sem certificado; segredo abaixo bloqueia acesso direto
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  # Tudo dinâmico (páginas, API, uploads): sem cache, repassa cookies/headers/query.
  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = local.cf_caching_disabled
    origin_request_policy_id = local.cf_all_viewer_except_host
  }

  # Assets versionados do Next (hash no nome): cache longo na borda.
  ordered_cache_behavior {
    path_pattern           = "/_next/static/*"
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = local.cf_caching_optimized
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "app_url" {
  description = "URL HTTPS da homologação (front + API)"
  value       = local.edge ? "https://${aws_cloudfront_distribution.edge[0].domain_name}" : "(desligado: enable_runtime/enable_edge)"
}

output "web_ecr_repository_url" {
  description = "URL para docker push da imagem do front"
  value       = aws_ecr_repository.web.repository_url
}
