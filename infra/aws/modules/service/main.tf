# modules/service/main.tf -- un service Ratis : ECR + logs + task def Fargate + ECS service
# + (si public) target group + regle de listener ALB path-based.

locals {
  # Conteneur unique nomme "app".
  # - env NON sensible -> bloc `environment` : liste de { name, value }.
  container_env = [
    for k, v in var.env : { name = k, value = v }
  ]
  # - secrets -> bloc `secrets` : liste de { name, valueFrom = ARN Secrets Manager }.
  #   ECS resout la valeur au demarrage du conteneur ; elle n'apparait jamais en
  #   clair dans la task definition (cf KP-securite : pas de secret en plaintext).
  container_secrets = [
    for k, arn in var.secrets : { name = k, valueFrom = arn }
  ]
}

# Repo ECR pour la future vraie image (la task tourne nginx pour l'instant).
resource "aws_ecr_repository" "this" {
  name = "ratis-${var.name}"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/ratis-${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "this" {
  family                   = "ratis-${var.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.exec_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.image
      essential = true
      portMappings = [
        {
          # 'name' requis par Service Connect pour referencer ce port.
          name          = "app-${var.container_port}"
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]
      environment = local.container_env
      secrets     = local.container_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

# ---- exposition publique optionnelle (ALB) ----

resource "aws_lb_target_group" "this" {
  count       = var.is_public ? 1 : 0
  name        = "ratis-${var.name}-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # obligatoire pour Fargate (reseau awsvpc)

  health_check {
    path                = "/"
    matcher             = "200-399"
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener_rule" "this" {
  count        = var.is_public ? 1 : 0
  listener_arn = var.alb_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[0].arn
  }

  condition {
    path_pattern {
      values = var.path_patterns
    }
  }
}

# ---- service ECS ----

resource "aws_ecs_service" "this" {
  name            = "ratis-${var.name}-svc"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnets
    security_groups  = [var.task_sg_id]
    assign_public_ip = true # pas de NAT : IP publique requise pour tirer l'image
  }

  # Service Connect : ce service s'enregistre sous <name>.ratis.local et peut
  # joindre les autres via leur nom interne. Vaut pour public ET interne.
  service_connect_configuration {
    enabled   = true
    namespace = var.service_connect_namespace_arn

    service {
      port_name = "app-${var.container_port}"
      client_alias {
        port     = var.container_port
        dns_name = var.name
      }
    }
  }

  # Branchement ALB seulement si le service est public.
  dynamic "load_balancer" {
    for_each = var.is_public ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.this[0].arn
      container_name   = "app"
      container_port   = var.container_port
    }
  }

  depends_on = [aws_lb_listener_rule.this]
}
