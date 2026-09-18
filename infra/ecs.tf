# ─────────────────────────────────────────────────────────────────────────
# T28.2 a T28.5 — API em containers no AWS Fargate
#   T28.2 task definition + serviço ECS
#   T28.3 Application Load Balancer + health checks (/health)
#   T28.4 autoscaling por CPU
#   T28.5 rolling deploy (circuit breaker + rollback)
# Todo o bloco é criado apenas quando enable_runtime = true.
# ─────────────────────────────────────────────────────────────────────────

data "aws_region" "current" {}

resource "aws_cloudwatch_log_group" "api" {
  count             = var.enable_runtime ? 1 : 0
  name              = "/ecs/${local.name}-api"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "main" {
  count = var.enable_runtime ? 1 : 0
  name  = "${local.name}-cluster"
  setting {
    name  = "containerInsights"
    value = "disabled" # evita custo extra de métricas
  }
}

resource "aws_ecs_task_definition" "api" {
  count                    = var.enable_runtime ? 1 : 0
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
    essential = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "CORS_ORIGINS", value = "*" }
    ]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "JWT_SECRET", valueFrom = aws_secretsmanager_secret.jwt.arn }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api[0].name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "api"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/health')\" || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
  }])
}

# ── ALB (T28.3) ──────────────────────────────────────────────────────────
resource "aws_lb" "api" {
  count              = var.enable_runtime ? 1 : 0
  name               = "${local.name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  count       = var.enable_runtime ? 1 : 0
  name        = "${local.name}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  count             = var.enable_runtime ? 1 : 0
  load_balancer_arn = aws_lb.api[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

# ── Serviço ECS (T28.2 / T28.5) ───────────────────────────────────────────
resource "aws_ecs_service" "api" {
  count           = var.enable_runtime ? 1 : 0
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main[0].id
  task_definition = aws_ecs_task_definition.api[0].arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = true # necessário sem NAT para puxar imagem do ECR
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api[0].arn
    container_name   = "api"
    container_port   = 8000
  }

  # T28.5 — rollback automático em deploy com falha
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}

# ── Autoscaling por CPU (T28.4) ───────────────────────────────────────────
resource "aws_appautoscaling_target" "api" {
  count              = var.enable_runtime ? 1 : 0
  max_capacity       = 3
  min_capacity       = 1
  resource_id        = "service/${aws_ecs_cluster.main[0].name}/${aws_ecs_service.api[0].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  count              = var.enable_runtime ? 1 : 0
  name               = "${local.name}-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api[0].resource_id
  scalable_dimension = aws_appautoscaling_target.api[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.api[0].service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 70
    scale_in_cooldown  = 60
    scale_out_cooldown = 60
  }
}
