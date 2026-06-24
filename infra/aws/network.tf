# network.tf -- VPC par defaut reutilise + security groups partages

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# SG de l'ALB partage : ouvert au monde sur le port 80.
resource "aws_security_group" "alb" {
  name        = "${var.project}-alb-sg"
  description = "ALB inbound 80 from internet"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP depuis Internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# SG des tasks partage :
#  - inbound depuis l'ALB (trafic des services publics)
#  - self-ingress : les tasks se parlent entre elles via Service Connect (trafic intra-cluster)
resource "aws_security_group" "task" {
  name        = "${var.project}-task-sg"
  description = "Tasks inbound from ALB and from peers via Service Connect"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Regles d'ingress separees (evite un cycle : self-ingress reference le SG lui-meme).
resource "aws_security_group_rule" "task_from_alb" {
  description              = "All TCP from ALB security group"
  type                     = "ingress"
  from_port                = 0
  to_port                  = 65535
  protocol                 = "tcp"
  security_group_id        = aws_security_group.task.id
  source_security_group_id = aws_security_group.alb.id
}

resource "aws_security_group_rule" "task_self" {
  description       = "Intra cluster traffic for Service Connect"
  type              = "ingress"
  from_port         = 0
  to_port           = 65535
  protocol          = "tcp"
  security_group_id = aws_security_group.task.id
  self              = true
}
