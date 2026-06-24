# Template — fiche entité (HSP / DA / M / ARCH / Audit)

> Ce template décrit le corps d'une fiche entité. **C'est le cœur du
> rendu décideur.** Une page par entité canonique. La structure des 5
> sections est imposée et non-négociable — cohérence du lecteur.

---

## Body — à instancier

```markdown
<!-- ratis-export:{ENTITY_ID} -->

# {ENTITY_ID} — {SHORT_TITLE_FR}

> Source canonique : `{FILE_PATH}` (mis à jour par le repo, pas ici).
> Statut : **{STATUS_FR}** · Dernière reformulation : **{ISO_DATE}**.

## Quoi

{QUOI}

## Qui est concerné

{QUI}

## Quand

{QUAND}

## Pourquoi

{POURQUOI}

## Comment ça change pour eux

{COMMENT}

---

_Cette fiche est générée automatiquement. Pour corriger, modifier le
fichier source ci-dessus dans le repo, puis rejouer la skill
`notion-export`._
```

---

## Variables à substituer

| Placeholder       | Source                                                                    |
|-------------------|---------------------------------------------------------------------------|
| `{ENTITY_ID}`     | identifiant canonique (`HSP-3`, `DA-44`, `ARCH_AUTH`, …)                  |
| `{SHORT_TITLE_FR}` | 4-9 mots décideur — extrait / réécrit depuis le H2 canonique             |
| `{FILE_PATH}`     | `Section.file_path` retourné par `docs_get(id)`                          |
| `{STATUS_FR}`     | mapping cf `category-page.md` (Livré / En cours / Planifié / autre)      |
| `{ISO_DATE}`      | date du run YYYY-MM-DD                                                    |
| `{QUOI}`          | 1-3 phrases — quoi a été décidé / construit / planifié                   |
| `{QUI}`           | 1-3 phrases — qui est concerné (utilisateur / support / business / légal / investisseur) |
| `{QUAND}`         | 1 phrase — statut + ISO date si visible                                  |
| `{POURQUOI}`      | 2-4 phrases — motivation business / produit / sécurité / conformité      |
| `{COMMENT}`       | 1-3 phrases — concrètement ce qui change pour la personne concernée      |

Les 5 sections doivent **toujours** être présentes, même si une section
est courte. Si une section ne peut pas être renseignée (ex: « Quand »
inconnu) → mettre `_Information non disponible dans la source
canonique._` plutôt que de l'omettre. Le lecteur attend la même
structure d'une fiche à l'autre.

## Règles de reformulation

> Voir `helpers/prompt-fragments.md` pour les prompts précis. Rappels
> impératifs ici pour que l'agent ne dérive pas.

1. **Pas de termes techniques bruts.** Lister-en quelques uns en
   miroir : non → `JWT`, oui → « jeton de connexion ». Non → `rôle PG`,
   oui → « droit d'accès en base de données ». Non → `HMAC`, oui →
   « empreinte cryptographique ». Non → `migration Alembic`, oui →
   « mise à jour de la base ».
2. **Pas de chemins de fichiers, pas de blocs de code, pas de noms de
   variables d'environnement.** Si une phrase canonique en contient,
   la reformuler autour de l'intention business.
3. **Pas de jargon agent / IA.** Non → « subagent », « MCP », « LLM ».
   Oui → « automatisation », « outil interne », « assistant ».
4. **Pas de numéros de PR / commits / branches** dans le body
   reformulé (sauf dans la ligne « Source canonique » du header, où la
   trace technique est utile). Le lecteur décideur n'a pas besoin de
   `#539`.
5. **Phrases courtes (≤ 25 mots).** Si une phrase est plus longue, la
   couper.
6. **Vocabulaire business autorisé** : solde cashback, scan, paiement,
   carte cadeau, notification, connexion utilisateur, sécurité,
   conformité RGPD, performance, disponibilité.

## Sentinel

`<!-- ratis-export:{ENTITY_ID} -->` doit être la **première ligne** du
body. C'est ce que la skill cherche dans le premier block des sous-pages
de catégorie pour matcher au prochain run et faire un update plutôt
qu'un duplicata.

## Length budget

- Header (titre + métadonnées) : ~3 lignes.
- Body (5 sections) : 200-400 mots total. Plus long = surcharge,
  contre-productif pour un décideur.
- Footer : 1 ligne d'instruction.

Si une section déborde, la couper. Si plusieurs sections débordent,
c'est probablement que la fiche canonique est dense — créer une fiche
résumée plutôt qu'un dump.
