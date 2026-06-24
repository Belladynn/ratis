# cluster.tf -- cluster ECS + Service Connect (Cloud Map) + role d'execution partage

resource "aws_ecs_cluster" "this" {
  name = "${var.project}-cluster"
}

# Namespace Cloud Map prive : support DNS de Service Connect (<service>.ratis.local).
resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = "ratis.local"
  description = "Service Connect namespace for Ratis services"
  vpc         = data.aws_vpc.default.id
}

# Role d'execution Fargate partage par tous les services (cree par Terraform).
resource "aws_iam_role" "execution" {
  name = "${var.project}-exec-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# La policy managee ci-dessus couvre le pull d'image + les logs, mais PAS la
# lecture Secrets Manager. Pour que `valueFrom` resolve les secrets au demarrage
# des tasks, on accorde GetSecretValue scope STRICTEMENT aux ARNs injectes
# (least-privilege : pas de "Resource": "*"). Les ARNs viennent de secrets.tf.
resource "aws_iam_role_policy" "execution_read_secrets" {
  name = "${var.project}-exec-read-secrets"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadInjectedSecrets"
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = values(local.secret_arns)
    }]
  })
}
