# versions.tf -- contraintes de version du module reutilisable `service`.
# Un module reutilisable declare ses propres exigences pour que le consommateur
# connaisse sa compatibilite (TFLint terraform_required_version/_providers).
# Le module n'utilise que le provider aws.

terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 6.0"
    }
  }
}
