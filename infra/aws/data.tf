# data.tf -- couche data partagee : RDS PostgreSQL 16 + ElastiCache Redis 7
# (representatif/minimal : prive, verrouille sur le SG des tasks)

# ---- RDS PostgreSQL 16 ----

resource "random_password" "rds" {
  length  = 24
  special = false # evite les caracteres a echapper dans l'URL
}

resource "aws_db_subnet_group" "rds" {
  name       = "${var.project}-rds-subnets"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-rds-sg"
  description = "RDS reachable only from ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "PostgreSQL from ECS tasks only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "this" {
  identifier             = "${var.project}-pg"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t3.micro"
  allocated_storage      = 20
  db_name                = "ratis"
  username               = "ratis"
  password               = random_password.rds.result
  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  skip_final_snapshot    = true # POC : pas de snapshot final
  apply_immediately      = true
}

# ---- ElastiCache Redis 7 ----

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project}-redis-subnets"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_security_group" "redis" {
  name        = "${var.project}-redis-sg"
  description = "Redis reachable only from ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "Redis from ECS tasks only"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_cluster" "this" {
  cluster_id         = "${var.project}-redis"
  engine             = "redis"
  engine_version     = "7.1"
  node_type          = "cache.t3.micro"
  num_cache_nodes    = 1
  port               = 6379
  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]
}
