# .tflint.hcl -- config TFLint pour infra/aws.
# Lance par la CI (cf .github/workflows/terraform.yml) : `tflint --init` installe
# les plugins ci-dessous, puis `tflint --chdir=infra/aws` analyse le code.
# Aucun credential AWS requis : TFLint fait de l'analyse statique, il n'appelle
# pas l'API AWS (le ruleset "aws" lint la config, pas le compte).

config {
  # Inspecte aussi les modules appeles (./modules/service).
  call_module_type = "all"
  force            = false
}

# --- ruleset Terraform de base (style, deprecations, conventions) ---
# Livre avec TFLint ; on l'active explicitement au preset recommande.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# --- ruleset AWS (provider-aware : types d'instances, ARNs, regles SG, etc.) ---
# Version pinnee pour des resultats CI reproductibles.
plugin "aws" {
  enabled = true
  version = "0.47.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

# --- regles de base de qualite, en complement des presets ---

# Variables/outputs/locals documentes et utilises.
rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}

# Pin des sources de modules (ici modules locaux -> non bloquant, mais on garde
# la regle active pour les futurs modules distants).
rule "terraform_module_pinned_source" {
  enabled = true
}

# Convention de nommage snake_case sur les identifiants Terraform.
rule "terraform_naming_convention" {
  enabled = true
  format  = "snake_case"
}
