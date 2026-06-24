# modules/service/variables.tf -- contrat d'entree d'un service Ratis

variable "name" {
  description = "Nom court du service (ex. auth, product) -- sert de prefixe de nommage"
  type        = string
}

variable "container_port" {
  description = "Port ecoute par le conteneur"
  type        = number
}

variable "image" {
  description = "Image du conteneur (nginx representatif pour le POC ; vraie image Ratis = phase ulterieure)"
  type        = string
  default     = "public.ecr.aws/docker/library/nginx:alpine"
}

variable "path_patterns" {
  description = "Patterns de chemin route par l'ALB vers ce service (ignore si is_public = false)"
  type        = list(string)
  default     = []
}

variable "cpu" {
  description = "vCPU de la task (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}

variable "memory" {
  description = "Memoire de la task en MiB"
  type        = number
  default     = 512
}

variable "env" {
  description = "Variables d'environnement NON sensibles injectees en clair dans le conteneur (map cle->valeur). Pour tout secret, utiliser var.secrets."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Secrets injectes dans le conteneur via ECS `secrets` -> `valueFrom` (map NOM_VAR -> ARN Secrets Manager). La valeur n'est jamais en clair dans la task definition : ECS la resout au demarrage. Le role d'execution doit pouvoir lire ces ARNs (cf execution_secrets_policy_arns)."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "is_public" {
  description = "true = expose via l'ALB (target group + regle de listener). false = joignable seulement en interne via Service Connect"
  type        = bool
  default     = true
}

variable "listener_rule_priority" {
  description = "Priorite de la regle de listener ALB (doit etre unique par listener ; ignore si is_public = false)"
  type        = number
  default     = 100
}

# ---- references partagees (injectees par la racine) ----

variable "vpc_id" {
  description = "ID du VPC partage"
  type        = string
}

variable "subnets" {
  description = "Liste des subnets pour le service ECS (et le target group)"
  type        = list(string)
}

variable "cluster_id" {
  description = "ID/ARN du cluster ECS partage"
  type        = string
}

variable "alb_listener_arn" {
  description = "ARN du listener HTTP partage de l'ALB (utilise seulement si is_public)"
  type        = string
  default     = ""
}

variable "exec_role_arn" {
  description = "ARN du role d'execution Fargate partage"
  type        = string
}

variable "task_sg_id" {
  description = "ID du security group des tasks (partage)"
  type        = string
}

variable "region" {
  description = "Region AWS (pour la config des logs)"
  type        = string
}

variable "service_connect_namespace_arn" {
  description = "ARN du namespace Cloud Map (Service Connect) pour la decouverte interne"
  type        = string
}

variable "log_retention_days" {
  description = "Retention des logs CloudWatch en jours"
  type        = number
  default     = 7
}
