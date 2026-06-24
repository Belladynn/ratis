"""Recreate the unique indexes on stores dropped during OSM bulk import.

Revision ID: 20260427_1700_recreate_stores_uq
Revises: 20260427_1500_scan_debug_v2
Create Date: 2026-04-27 17:00:00.000000+00:00

Context (2026-04-27) : the OSM Geofabrik bulk import (`osm_bulk_import.py`,
58 921 stores en 75 s) butait sur trois unique indexes — `unique_store`,
`uq_stores_phone`, `uq_stores_siret` — parce que `normalize.upsert_store`
n'avait qu'un seul `ON CONFLICT (osm_id)` côté INSERT et ne gérait pas les
collisions sur ces autres invariants. Pour débloquer l'import, les 3 indexes
ont été DROP en prod.

Décision produit du 2026-04-27 (refactor/phone-as-retailer-signal) :
le téléphone n'est PAS unique par magasin. Plusieurs magasins partagent
souvent un standard corporate (franchise enseigne). On garde donc les
duplicates phone et on ne recrée PAS `uq_stores_phone` — le champ devient
un attribut indicatif (signal pour inférer le retailer côté OCR).

Cette migration recrée seulement `unique_store` et `uq_stores_siret`
idempotamment via `CREATE UNIQUE INDEX IF NOT EXISTS`. Elle est sûre à
appliquer plusieurs fois et n'échoue pas si un index existe déjà.

Pré-requis avant `alembic upgrade head` en prod :
  1. Code déployé avec `upsert_store` qui gère les 3 invariants restants.
  2. Re-run `osm_bulk_import.py` (idempotent) pour normaliser les données
     résiduelles avec la nouvelle logique multi-conflit.
  3. Exécuter cette migration.
  4. Vérifier `\d stores` : `unique_store` et `uq_stores_siret` doivent
     exister, `uq_stores_phone` doit rester absent.

Si des doublons siret subsistent au moment de la création, PostgreSQL
refusera la création de l'index. Dans ce cas : identifier les doublons
(`SELECT siret, COUNT(*) FROM stores WHERE siret IS NOT NULL GROUP BY 1
HAVING COUNT(*)>1`), décider manuellement quel rang garder, puis relancer.
"""
from __future__ import annotations

from alembic import op

revision = "20260427_1700_recreate_stores_uq"
down_revision = "20260427_1500_scan_debug_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive : if a previous version of this migration (or an older state
    # of the DB) recreated `uq_stores_phone`, drop it now — phone is no
    # longer unique per the 2026-04-27 retailer-signal refactor.
    op.execute("DROP INDEX IF EXISTS uq_stores_phone")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_stores_siret "
        "ON stores(siret) WHERE siret IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS unique_store ON stores "
        "(COALESCE(retailer, ''), COALESCE(address, ''), COALESCE(postal_code, ''))"
    )


def downgrade() -> None:
    # Symetric drop — same shape as the original drop performed in prod on
    # 2026-04-27. Idempotent.
    op.execute("DROP INDEX IF EXISTS unique_store")
    op.execute("DROP INDEX IF EXISTS uq_stores_siret")
    # uq_stores_phone is no longer recreated upgrade — drop here is still
    # safe (idempotent) for callers downgrading from an earlier state.
    op.execute("DROP INDEX IF EXISTS uq_stores_phone")
