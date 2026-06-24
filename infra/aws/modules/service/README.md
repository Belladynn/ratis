# `service` module

Reusable building block for **one Ratis backend service** on AWS ECS Fargate.
Instantiated 5× from the root config (`infra/aws/services.tf`) — one per service
(auth, product, list, rewards, notifier).

## Purpose

Each instance provisions, for a single service:

- an **ECR repository** (`ratis-<name>`) for the future real image;
- a **CloudWatch log group** (`/ecs/ratis-<name>`) with configurable retention;
- a **Fargate task definition** (single `app` container) wiring:
  - non-sensitive env via the `environment` block (`var.env`),
  - **secrets via the `secrets` block → `valueFrom`** (`var.secrets`, Secrets
    Manager ARNs) — values are resolved by ECS at container start and never
    appear in plaintext in the task definition;
- an **ECS service** (`desired_count = 1`, `FARGATE`) registered in **Service
  Connect** under `<name>.ratis.local` for internal service-to-service calls;
- **only if `is_public = true`**: an ALB **target group** + a path-based
  **listener rule** on the shared ALB listener.

Internal-only services (`is_public = false`, e.g. notifier) are reachable solely
via Service Connect — never through the ALB.

> Secrets handling: the execution role must be allowed to read the ARNs passed in
> `var.secrets`. That grant is scoped (least-privilege) on the shared execution
> role at the root (`infra/aws/cluster.tf`), not inside this module.

## Usage

```hcl
module "auth" {
  source = "./modules/service"

  name                   = "auth"
  container_port         = 8001
  image                  = var.default_image
  is_public              = true
  path_patterns          = ["/api/v1/auth/*", "/account/*", "/webhooks/*"]
  listener_rule_priority = 10

  env     = { NOTIFIER_URL = local.notifier_url } # non-sensitive
  secrets = local.common_secrets                  # name -> Secrets Manager ARN

  # shared references injected by the root
  vpc_id                        = local.shared.vpc_id
  subnets                       = local.shared.subnets
  cluster_id                    = local.shared.cluster_id
  alb_listener_arn              = local.shared.alb_listener_arn
  exec_role_arn                 = local.shared.exec_role_arn
  task_sg_id                    = local.shared.task_sg_id
  region                        = local.shared.region
  service_connect_namespace_arn = local.shared.service_connect_namespace_arn
}
```

## Requirements

| Name      | Version   |
| --------- | --------- |
| terraform | >= 1.11   |
| aws       | >= 5.0, < 6.0 |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| `name` | Short service name (e.g. `auth`, `product`) — used as a naming prefix. | `string` | n/a | yes |
| `container_port` | Port the container listens on. | `number` | n/a | yes |
| `image` | Container image (representative nginx for the POC; real Ratis image is a later phase). | `string` | `"public.ecr.aws/docker/library/nginx:alpine"` | no |
| `path_patterns` | ALB path patterns routed to this service (ignored when `is_public = false`). | `list(string)` | `[]` | no |
| `cpu` | Task vCPU (256 = 0.25 vCPU). | `number` | `256` | no |
| `memory` | Task memory in MiB. | `number` | `512` | no |
| `env` | **Non-sensitive** environment variables injected as plaintext (`name -> value`). Use `secrets` for anything sensitive. | `map(string)` | `{}` | no |
| `secrets` | Secrets injected via ECS `secrets` → `valueFrom` (`VAR_NAME -> Secrets Manager ARN`). Never plaintext in the task def; ECS resolves at start. The execution role must be allowed to read these ARNs. **Sensitive.** | `map(string)` | `{}` | no |
| `is_public` | `true` = exposed via the ALB (target group + listener rule); `false` = internal-only via Service Connect. | `bool` | `true` | no |
| `listener_rule_priority` | ALB listener-rule priority (must be unique per listener; ignored when `is_public = false`). | `number` | `100` | no |
| `vpc_id` | ID of the shared VPC. | `string` | n/a | yes |
| `subnets` | Subnets for the ECS service (and target group). | `list(string)` | n/a | yes |
| `cluster_id` | ID/ARN of the shared ECS cluster. | `string` | n/a | yes |
| `alb_listener_arn` | ARN of the shared ALB HTTP listener (used only when `is_public`). | `string` | `""` | no |
| `exec_role_arn` | ARN of the shared Fargate execution role. | `string` | n/a | yes |
| `task_sg_id` | ID of the shared tasks security group. | `string` | n/a | yes |
| `region` | AWS region (for the log configuration). | `string` | n/a | yes |
| `service_connect_namespace_arn` | ARN of the Cloud Map (Service Connect) namespace for internal discovery. | `string` | n/a | yes |
| `log_retention_days` | CloudWatch log retention in days. | `number` | `7` | no |

## Outputs

| Name | Description | Sensitive |
| ---- | ----------- | :-------: |
| `ecr_repo_url` | URL of this service's ECR repository. | no |
| `service_name` | Name of the ECS service. | no |
| `internal_dns` | Internal Service Connect DNS name (`<name>.ratis.local:<port>`). | no |
| `target_group_arn` | Target group ARN (`null` when the service is internal). | no |
| `secret_arns` | Secrets Manager ARNs injected via `valueFrom` (the execution role must be able to read them). | yes |
