"""Run-once script: generate tests/fixtures/stock_etab_sample.parquet.

Produces a synthetic SIRENE Parquet file with 100 rows covering:
  - 30 active food APE (etatAdministratif='A', APE 47.11B/47.11D/47.21Z)
  - 10 closed food APE (etatAdministratif='F', with and without dateFermeture)
  - 10 non-food active (47.51Z textile)
  -  5 active food, enseigne1Etablissement NULL (fallback to denomination)
  -  5 active food, numeroVoie NULL (partial address)
  Rest: 40 active food rows with variant APE codes (47.22Z, 47.29Z, etc.)

Row group size is 50 — small enough that iter_batches() chunking is exercised
in tests without needing a huge fixture.

Usage (run once from repo root):
    python batch/ratis_batch_sirene_sync/tests/fixtures/_generate_sample.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script from repo root.
FIXTURE_DIR = Path(__file__).parent
PARQUET_OUT = FIXTURE_DIR / "stock_etab_sample.parquet"

# Row group size for the fixture — small to exercise iter_batches() in tests.
ROW_GROUP_SIZE = 50


def _make_row(
    i: int,
    *,
    ape: str,
    state: str = "A",
    date_fermeture: str | None = None,
    enseigne1: str | None = "Épicerie Test {i}",
    denom_usuelle: str | None = None,
    denom_legale: str | None = "SAS TESTCO {i}",
    numero_voie: str | None = "12",
    type_voie: str = "RUE",
    libelle_voie: str = "DE LA REPUBLIQUE",
    ville: str = "PARIS",
    code_postal: str = "75001",
    cat_juridique: str = "5499",
    siret: str | None = None,
) -> dict:
    siret_val = siret or f"{i:014d}"
    return {
        "siret": siret_val,
        "siren": siret_val[:9],
        "nic": siret_val[9:],
        "etatAdministratifEtablissement": state,
        "dateFermetureEtablissement": date_fermeture,
        "activitePrincipaleEtablissement": ape,
        "enseigne1Etablissement": enseigne1.format(i=i) if enseigne1 else None,
        "denominationUsuelleEtablissement": denom_usuelle.format(i=i) if denom_usuelle else None,
        "denominationUniteLegale": denom_legale.format(i=i) if denom_legale else None,
        "numeroVoie": numero_voie,
        "typeVoie": type_voie,
        "libelleVoie": libelle_voie,
        "libelleCommuneEtablissement": ville,
        "codePostalEtablissement": code_postal,
        "categorieJuridiqueUniteLegale": cat_juridique,
    }


def build_rows() -> list[dict]:
    rows: list[dict] = []
    idx = 1

    # 30 active food APE (10 each of 47.11B, 47.11D, 47.21Z)
    for ape in ("47.11B", "47.11D", "47.21Z"):
        for _ in range(10):
            rows.append(_make_row(idx, ape=ape, state="A"))
            idx += 1

    # 10 closed food APE (5 with dateFermeture, 5 without)
    for j in range(10):
        date_f = "2024-03-15" if j < 5 else None
        rows.append(
            _make_row(
                idx,
                ape="47.11B",
                state="F",
                date_fermeture=date_f,
            )
        )
        idx += 1

    # 10 non-food active (47.51Z textile) — should NOT pass APE filter
    for _ in range(10):
        rows.append(_make_row(idx, ape="47.51Z", state="A"))
        idx += 1

    # 5 active food, enseigne1=None — should fall back to denominationUsuelle
    for _ in range(5):
        rows.append(
            _make_row(
                idx,
                ape="47.11C",
                enseigne1=None,
                denom_usuelle="DenomUsuelle {i}",
            )
        )
        idx += 1

    # 5 active food, numeroVoie=None — partial address, no crash
    for _ in range(5):
        rows.append(
            _make_row(
                idx,
                ape="47.11D",
                numero_voie=None,
            )
        )
        idx += 1

    # Fill remaining rows to reach 100 with varied APE (47.22Z, 47.29Z, etc.)
    extra_apes = ["47.22Z", "47.29Z", "47.81Z", "47.23Z", "47.24Z", "47.25Z", "47.11E", "47.11F"]
    while len(rows) < 100:
        ape = extra_apes[(idx - 1) % len(extra_apes)]
        rows.append(_make_row(idx, ape=ape, state="A"))
        idx += 1

    return rows[:100]


def main() -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = build_rows()

    # Build a pyarrow Table from the list of dicts.
    # All columns are strings (or None) to mirror the output of
    # download._convert_csv_to_parquet() which casts everything to pa.string().
    col_names = list(rows[0].keys())
    arrays = {}
    for col in col_names:
        values = [r.get(col) for r in rows]
        arrays[col] = pa.array(values, type=pa.string())

    table = pa.table(arrays)
    pq.write_table(
        table,
        str(PARQUET_OUT),
        row_group_size=ROW_GROUP_SIZE,
        compression="snappy",
    )
    print(f"Written {len(rows)} rows to {PARQUET_OUT} (row_group_size={ROW_GROUP_SIZE})")


if __name__ == "__main__":
    main()
    sys.exit(0)
