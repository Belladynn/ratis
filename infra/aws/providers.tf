# providers.tf -- configuration du provider AWS.
# Region/profil parametres (cf variables.tf). En CI publique sans compte AWS,
# `terraform validate` n'exige aucun credential -- le provider n'est contacte
# qu'au plan/apply.

provider "aws" {
  region  = var.region
  profile = var.profile

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
