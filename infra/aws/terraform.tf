# terraform.tf -- version Terraform + providers requis (pin des versions).
# Le backend d'etat distant vit dans backend.tf ; la config provider dans providers.tf.

terraform {
  # >= 1.11 : verrouillage d'etat S3 natif (use_lockfile, cf backend.tf) sans DynamoDB.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.52"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}
