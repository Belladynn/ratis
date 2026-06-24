# Template — page catégorie

> Ce template décrit le corps d'une des 5 sous-pages de catégorie sous
> la racine « État du projet Ratis ». Chaque page de catégorie est un
> simple index : titre + intro courte + liste des entités enfants. Les
> détails vivent dans les pages enfants (cf `entity-page.md`).

---

## Body — à instancier

```markdown
<!-- ratis-export:CATEGORY:{SLUG} -->

# {TITLE}

_{INTRO}_

Cette page liste les entrées de la catégorie « {TITLE_SHORT} ».
Chaque ligne est un lien vers une fiche détaillée. Dernière mise à
jour : **{ISO_DATE}**.

## Entrées

| Identifiant | Titre court | Statut | Date |
|---|---|---|---|
{ROWS}
```

---

## 5 instances — paramètres par catégorie

| Slug         | TITLE                                  | TITLE_SHORT          | INTRO                                                                                                                                |
|--------------|----------------------------------------|----------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `arch`       | Architecture & Modules                 | Architecture         | Comment l'application est structurée. Un par grand bloc (service backend, app mobile, base de données, déploiement).                |
| `decisions`  | Décisions actées (DA)                  | Décisions            | Choix produit ou techniques validés, datés, irréversibles. Chaque DA porte un numéro et un contexte d'application.                  |
| `subprojects`| Sous-projets en cours (HSP / M)        | Sous-projets         | Initiatives techniques durcies (HSP) ou modules (M). En cours, livrés V1.x, ou planifiés.                                            |
| `audits`     | Audits & contexte                      | Audits               | Analyses externes ou internes ayant motivé des décisions. Précieux pour comprendre le pourquoi des HSP / DA.                        |
| `known`      | Problèmes connus (mémoire)             | Problèmes connus     | Carnet de mémoire de l'équipe : ce qui ne marche pas (encore), les pièges, les sujets à reprendre. V1 : résumé index uniquement.    |

## Variables à substituer

| Placeholder | Source                                                          |
|-------------|-----------------------------------------------------------------|
| `{SLUG}`     | un des 5 slugs ci-dessus                                       |
| `{TITLE}`    | colonne TITLE de la catégorie                                  |
| `{TITLE_SHORT}` | colonne TITLE_SHORT de la catégorie                         |
| `{INTRO}`    | colonne INTRO de la catégorie                                  |
| `{ISO_DATE}` | Date du run au format YYYY-MM-DD                               |
| `{ROWS}`     | Lignes du tableau, format ci-dessous, une par entité dans cette catégorie |

## Format d'une ligne `{ROWS}`

```
| [{ID}](#{ID_anchor}) | {SHORT_TITLE} | {STATUS_FR} | {DATE_OR_DASH} |
```

Avec :
- `{ID}` = identifiant brut (`HSP-3`, `DA-44`, `ARCH_AUTH`, …).
- `{ID_anchor}` = ancre Notion vers la sous-page enfant. Notion remplace
  automatiquement par un lien interne quand la sous-page existe.
- `{SHORT_TITLE}` = 1-7 mots décideur, extrait de la 1ʳᵉ phrase « Quoi »
  de la fiche enfant (cf `entity-page.md`).
- `{STATUS_FR}` = mapping :
  - `LIVRÉ V1.1` / `LIVRÉ V1.0` / `LIVRÉ V0` → `Livré`
  - `EN-COURS` → `En cours`
  - `PLANIFIÉ` → `Planifié`
  - autre → la chaîne canonique brute (fallback)
- `{DATE_OR_DASH}` = ISO date si lisible dans le canonical body
  (« 2026-05-21 », « V1.1 » → laisser tel quel), sinon `—`.

## Sentinel

`<!-- ratis-export:CATEGORY:{SLUG} -->` doit être la première ligne du
body. Permet à la skill de retrouver la page catégorie au prochain run
même si le titre a été renommé.

## Tri des lignes

- Sous-projets : par identifiant numérique croissant (HSP-1, HSP-2,
  …, M-1, M-2, …).
- Décisions : par identifiant numérique décroissant (DA-50 d'abord, DA-1
  en bas — les dernières décisions priment).
- Architecture / Audits : par titre alphabétique du fichier source.
- Problèmes connus : pas de rows en V1 (la page contient seulement
  l'index résumé, pas d'enfants).
