# modules/service/outputs.tf

output "ecr_repo_url" {
  description = "URL du repo ECR de ce service"
  value       = aws_ecr_repository.this.repository_url
}

output "service_name" {
  description = "Nom du service ECS"
  value       = aws_ecs_service.this.name
}

output "internal_dns" {
  description = "Nom DNS interne Service Connect (<name>.ratis.local:<port>)"
  value       = "${var.name}.ratis.local:${var.container_port}"
}

output "target_group_arn" {
  description = "ARN du target group (null si service interne)"
  value       = var.is_public ? aws_lb_target_group.this[0].arn : null
}

output "secret_arns" {
  description = "ARNs Secrets Manager injectes dans ce service via valueFrom (le role d'execution doit pouvoir les lire). Sensible : expose les ARNs des secrets."
  value       = values(var.secrets)
  sensitive   = true
}
