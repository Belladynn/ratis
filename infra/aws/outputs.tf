# outputs.tf

output "alb_url" {
  description = "URL publique partagee (route path-based vers les 4 services publics)"
  value       = "http://${aws_lb.this.dns_name}"
}

output "rds_endpoint" {
  description = "Endpoint RDS PostgreSQL (host:port, prive)"
  value       = aws_db_instance.this.endpoint
}

output "redis_endpoint" {
  description = "Endpoint primaire ElastiCache Redis (prive)"
  value       = aws_elasticache_cluster.this.cache_nodes[0].address
}

output "ecr_repo_urls" {
  description = "URL des repos ECR par service"
  value = {
    auth     = module.auth.ecr_repo_url
    product  = module.product.ecr_repo_url
    list     = module.list.ecr_repo_url
    rewards  = module.rewards.ecr_repo_url
    notifier = module.notifier.ecr_repo_url
  }
}

output "internal_dns" {
  description = "Noms DNS internes Service Connect par service"
  value = {
    auth     = module.auth.internal_dns
    product  = module.product.internal_dns
    list     = module.list.internal_dns
    rewards  = module.rewards.internal_dns
    notifier = module.notifier.internal_dns
  }
}

output "service_connect_namespace" {
  description = "Namespace Cloud Map prive utilise par Service Connect"
  value       = aws_service_discovery_private_dns_namespace.this.name
}
