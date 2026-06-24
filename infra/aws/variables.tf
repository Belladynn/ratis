# variables.tf -- variables d'entree de la racine.

variable "region" {
  description = "Region AWS"
  type        = string
  default     = "eu-west-3"
}

variable "profile" {
  description = "Profil AWS CLI utilise par le provider"
  type        = string
  default     = "claude-agent"
}

variable "project" {
  description = "Prefixe de nommage global du POC"
  type        = string
  default     = "ratis"
}

variable "default_image" {
  description = "Image representative par defaut pour toutes les tasks (les vraies images Ratis = phase ulterieure)"
  type        = string
  default     = "public.ecr.aws/docker/library/nginx:alpine"
}
