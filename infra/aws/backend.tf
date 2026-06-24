# backend.tf -- etat Terraform distant sur S3.
#
# Verrouillage d'etat NATIF S3 (use_lockfile, Terraform >= 1.11) : un fichier
# <key>.tflock pose sur le bucket via une ecriture conditionnelle -- pas de table
# DynamoDB a provisionner ni a maintenir.
#
# NON initialise en CI : ce repo est public et n'a aucun compte AWS cable. Le
# job CI tourne `terraform init -backend=false` (cf .github/workflows/terraform.yml),
# donc ce bloc n'est jamais contacte par la validation. Pour un vrai deploiement :
# remplacer le bucket placeholder ci-dessous par un bucket S3 reel (chiffre,
# versionne, Block Public Access on) puis `terraform init -reconfigure`.
#
# La cle d'etat est par-environnement (var inlinable a l'init via `-backend-config`
# ou un fichier *.s3.tfbackend) pour isoler dev/staging/prod sur le meme bucket :
#   terraform init -backend-config="key=ratis/prod/terraform.tfstate"

terraform {
  backend "s3" {
    # PLACEHOLDER -- remplacer par un vrai bucket d'etat avant tout deploiement.
    # (bucket non cree par ce code : un bucket d'etat se provisionne hors-stack
    #  pour eviter la dependance circulaire etat <-> backend.)
    bucket = "ratis-tfstate-REPLACE-ME" # pragma: allowlist secret

    key          = "ratis/dev/terraform.tfstate" # surcharge par env via -backend-config
    region       = "eu-west-3"
    encrypt      = true # chiffrement SSE de l'objet d'etat au repos
    use_lockfile = true # verrou d'etat natif S3 (pas de DynamoDB)
  }
}
