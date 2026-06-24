# secrets.tf -- Secrets Manager : INTERNAL_API_KEY + DATABASE_URL + REDIS_URL
# Les valeurs sont composees depuis RDS/ElastiCache puis injectees dans les tasks
# via ECS `secrets` -> `valueFrom` (ARN Secrets Manager) -- JAMAIS en clair dans la
# task definition. Le role d'execution recoit une policy de lecture scopee a ces
# ARNs (cf cluster.tf). C'est la frontiere durcie attendue pour un service qui
# fait tourner un coffre a secrets.

resource "random_password" "internal_api_key" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "internal_api_key" {
  name = "${var.project}/internal-api-key"
}

resource "aws_secretsmanager_secret_version" "internal_api_key" {
  secret_id     = aws_secretsmanager_secret.internal_api_key.id
  secret_string = random_password.internal_api_key.result
}

# DATABASE_URL : format postgresql+psycopg, compose depuis RDS (voir version ci-dessous)
resource "aws_secretsmanager_secret" "database_url" {
  name = "${var.project}/database-url"
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql+psycopg://%s:%s@%s/%s", # pragma: allowlist secret
    aws_db_instance.this.username,
    random_password.rds.result,
    aws_db_instance.this.endpoint, # endpoint inclut deja :5432
    aws_db_instance.this.db_name,
  )
}

# REDIS_URL au format redis://host:6379/0
resource "aws_secretsmanager_secret" "redis_url" {
  name = "${var.project}/redis-url"
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id = aws_secretsmanager_secret.redis_url.id
  secret_string = format(
    "redis://%s:%s/0",
    aws_elasticache_cluster.this.cache_nodes[0].address,
    aws_elasticache_cluster.this.cache_nodes[0].port,
  )
}

# ARNs des secrets, reutilises par services.tf pour cabler les tasks via valueFrom
# et par cluster.tf pour scoper la policy de lecture du role d'execution.
# On reference l'ARN de la VERSION (...:::secret:name-AbCdEf) : c'est la forme
# attendue par ECS `valueFrom` et la plus stricte pour resource-scoper l'IAM.
locals {
  secret_arns = {
    DATABASE_URL     = aws_secretsmanager_secret_version.database_url.arn
    REDIS_URL        = aws_secretsmanager_secret_version.redis_url.arn
    INTERNAL_API_KEY = aws_secretsmanager_secret_version.internal_api_key.arn
  }
}
