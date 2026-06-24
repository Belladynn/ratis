# services.tf -- les 5 services Ratis, instances du module reutilisable.
# Images = nginx representatif (var.default_image). Les secrets partages
# (DATABASE_URL/REDIS_URL/INTERNAL_API_KEY) sont injectes via `secrets` (ARN
# Secrets Manager -> valueFrom), JAMAIS en clair. L'env en clair se limite au
# non-sensible (NOTIFIER_URL). Priorites de regle de listener distinctes (ALB).

locals {
  # Secrets communs a tous les services : map NOM -> ARN Secrets Manager.
  # Resolus par ECS au demarrage de la task (cf modules/service var.secrets).
  common_secrets = local.secret_arns

  # URL interne du notifier via Service Connect (joignable sans passer par l'ALB).
  # Non sensible -> reste dans `env` en clair.
  notifier_url = "http://notifier.ratis.local:8005"

  # References partagees passees a chaque instance du module.
  shared = {
    vpc_id                        = data.aws_vpc.default.id
    subnets                       = data.aws_subnets.default.ids
    cluster_id                    = aws_ecs_cluster.this.id
    alb_listener_arn              = aws_lb_listener.http.arn
    exec_role_arn                 = aws_iam_role.execution.arn
    task_sg_id                    = aws_security_group.task.id
    region                        = var.region
    service_connect_namespace_arn = aws_service_discovery_private_dns_namespace.this.arn
  }
}

# AU -- auth :8001 (public)
module "auth" {
  source = "./modules/service"

  name                   = "auth"
  container_port         = 8001
  image                  = var.default_image
  is_public              = true
  path_patterns          = ["/api/v1/auth/*", "/account/*", "/webhooks/*"]
  listener_rule_priority = 10
  env                    = { NOTIFIER_URL = local.notifier_url }
  secrets                = local.common_secrets

  vpc_id                        = local.shared.vpc_id
  subnets                       = local.shared.subnets
  cluster_id                    = local.shared.cluster_id
  alb_listener_arn              = local.shared.alb_listener_arn
  exec_role_arn                 = local.shared.exec_role_arn
  task_sg_id                    = local.shared.task_sg_id
  region                        = local.shared.region
  service_connect_namespace_arn = local.shared.service_connect_namespace_arn
}

# PA -- product analyser :8003 (public)
module "product" {
  source = "./modules/service"

  name                   = "product"
  container_port         = 8003
  image                  = var.default_image
  is_public              = true
  path_patterns          = ["/api/v1/scan/*", "/product/*"]
  listener_rule_priority = 20
  env                    = { NOTIFIER_URL = local.notifier_url }
  secrets                = local.common_secrets

  vpc_id                        = local.shared.vpc_id
  subnets                       = local.shared.subnets
  cluster_id                    = local.shared.cluster_id
  alb_listener_arn              = local.shared.alb_listener_arn
  exec_role_arn                 = local.shared.exec_role_arn
  task_sg_id                    = local.shared.task_sg_id
  region                        = local.shared.region
  service_connect_namespace_arn = local.shared.service_connect_namespace_arn
}

# LO -- list optimiser :8002 (public)
module "list" {
  source = "./modules/service"

  name                   = "list"
  container_port         = 8002
  image                  = var.default_image
  is_public              = true
  path_patterns          = ["/api/v1/lists/*"]
  listener_rule_priority = 30
  secrets                = local.common_secrets

  vpc_id                        = local.shared.vpc_id
  subnets                       = local.shared.subnets
  cluster_id                    = local.shared.cluster_id
  alb_listener_arn              = local.shared.alb_listener_arn
  exec_role_arn                 = local.shared.exec_role_arn
  task_sg_id                    = local.shared.task_sg_id
  region                        = local.shared.region
  service_connect_namespace_arn = local.shared.service_connect_namespace_arn
}

# RW -- rewards :8004 (public)
module "rewards" {
  source = "./modules/service"

  name                   = "rewards"
  container_port         = 8004
  image                  = var.default_image
  is_public              = true
  path_patterns          = ["/api/v1/gamification/*", "/rewards/*", "/admin/*"]
  listener_rule_priority = 40
  env                    = { NOTIFIER_URL = local.notifier_url }
  secrets                = local.common_secrets

  vpc_id                        = local.shared.vpc_id
  subnets                       = local.shared.subnets
  cluster_id                    = local.shared.cluster_id
  alb_listener_arn              = local.shared.alb_listener_arn
  exec_role_arn                 = local.shared.exec_role_arn
  task_sg_id                    = local.shared.task_sg_id
  region                        = local.shared.region
  service_connect_namespace_arn = local.shared.service_connect_namespace_arn
}

# NT -- notifier :8005 (INTERNE -- joignable seulement via Service Connect, pas sur l'ALB)
module "notifier" {
  source = "./modules/service"

  name           = "notifier"
  container_port = 8005
  image          = var.default_image
  is_public      = false
  secrets        = local.common_secrets

  vpc_id                        = local.shared.vpc_id
  subnets                       = local.shared.subnets
  cluster_id                    = local.shared.cluster_id
  alb_listener_arn              = local.shared.alb_listener_arn
  exec_role_arn                 = local.shared.exec_role_arn
  task_sg_id                    = local.shared.task_sg_id
  region                        = local.shared.region
  service_connect_namespace_arn = local.shared.service_connect_namespace_arn
}
