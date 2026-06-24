# Template — page racine « État du projet Ratis »

> Ce template décrit le corps Notion de la page racine. La skill l'instancie
> à chaque run (Step 5) avec les compteurs réels. Garder cette page concise :
> c'est le point d'entrée des stakeholders, pas un mur de texte.

---

## Body — à instancier

```markdown
<!-- ratis-export:ROOT -->

# État du projet Ratis

_Mirror automatique de la documentation canonique du repo, reformulée pour
décideurs et non-techniciens. Dernière mise à jour : **{ISO_DATE}**._

Ratis = cashback + prix en temps réel + jeu + cartes cadeau. Cette page
résume où en est le projet, qui décide quoi, et ce qui change pour les
utilisateurs.

## Sommaire

| Catégorie | Contenu | Entrées |
|---|---|---|
| **Architecture & Modules** | Comment l'application est structurée — services, bases de données, mobile | {N_ARCH} |
| **Décisions actées (DA)** | Choix produit ou techniques validés, datés, irréversibles | {N_DA} |
| **Sous-projets en cours (HSP / M)** | Initiatives identifiées, en cours ou livrées V1.x | {N_HSP} |
| **Audits & contexte** | Études d'impact, audits sécurité, analyses externes | {N_AUDIT} |
| **Problèmes connus** | Sujet de mémoire — ce que l'équipe sait qui ne marche pas (encore) | {N_KP} |

> Pour chaque entrée, cliquer sur le nom donne une fiche reformulée :
> **quoi · qui · quand · pourquoi · comment ça change pour eux**.

## Comment lire cette page

- Cette page est **générée automatiquement** depuis la documentation
  technique du projet ratis. Toute correction doit être faite à la
  source (le repo), pas ici — sinon elle sera écrasée au prochain run.
- Le jargon technique a été retiré ou traduit en intention business.
  Si une fiche est encore opaque, c'est un bug du processus de
  reformulation — signaler à l'opérateur.
- Les dates « livré V1.x » correspondent à des fonctionnalités en
  production. « En cours » = en développement actif. « Planifié » =
  validé mais pas encore commencé.
```

---

## Variables à substituer

| Placeholder | Source                                                              |
|-------------|---------------------------------------------------------------------|
| `{ISO_DATE}` | Date du run au format YYYY-MM-DD                                  |
| `{N_ARCH}`   | Nombre d'entrées dans la catégorie Architecture                  |
| `{N_DA}`     | Nombre d'entrées dans la catégorie Décisions                     |
| `{N_HSP}`    | Nombre d'entrées dans la catégorie Sous-projets                  |
| `{N_AUDIT}`  | Nombre d'entrées dans la catégorie Audits                        |
| `{N_KP}`     | 1 si la page « Problèmes connus » a été créée, 0 sinon (V1 scope) |

Le sentinel `<!-- ratis-export:ROOT -->` doit être la première ligne du
body. Il sert à la skill pour retrouver la page racine au prochain run
(au cas où le titre aurait été renommé manuellement).
