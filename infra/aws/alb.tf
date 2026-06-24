# alb.tf -- UN ALB partage + listener HTTP:80 (les regles path-based vivent dans le module)

resource "aws_lb" "this" {
  name               = "${var.project}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids # VPC par defaut >= 2 subnets : OK pour un ALB
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  # Action par defaut : 404. Chaque service public ajoute sa propre regle de
  # routage path-based (aws_lb_listener_rule dans le module).
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "404 - no route"
      status_code  = "404"
    }
  }
}
