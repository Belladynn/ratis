# main.tf -- point d'entree / carte de la racine.
#
# La config racine est decoupee par responsabilite (un fichier = un sujet) :
#   terraform.tf  -- bloc terraform{} : required_version + required_providers (pin)
#   providers.tf  -- provider "aws" (region/profil/default_tags)
#   backend.tf    -- backend S3 distant (etat + verrou natif via use_lockfile)
#   variables.tf  -- variables d'entree de la racine
#   outputs.tf    -- sorties de la racine
#
# Infrastructure (chaque fichier porte ses propres resources) :
#   network.tf    -- VPC par defaut + security groups partages (alb, task)
#   data.tf       -- couche data : RDS PostgreSQL 16 + ElastiCache Redis 7
#   secrets.tf    -- Secrets Manager : DATABASE_URL / REDIS_URL / INTERNAL_API_KEY
#   alb.tf        -- ALB partage + listener HTTP:80 (regles path-based dans le module)
#   cluster.tf    -- cluster ECS + Service Connect + role d'execution Fargate
#   services.tf   -- les 5 services Ratis, instances du module ./modules/service
#
# Aucune resource n'est declaree ici : ce fichier sert de sommaire. Le cablage
# concret (data -> secrets -> services) se lit dans services.tf.
