"""Source registry for the multi-source ingestion batch.

A `Source` describes everything that varies between OFF / OBP / OPF / OPFF :
  - API base URL          (Search API + product lookup)
  - Bulk JSONL dump URL   (--mode full bootstrap)
  - User-Agent string     (each project requires identification)
  - Photo CDN whitelist   (security — _safe_url drops anything else)
  - batch_sync_log name   (per-source resume cursor in delta mode)
  - Pydantic source value (written into products.source)
  - classify_storage      (food-only flag — drives storage_type derivation)

The shared business rules (extractor pipeline, upsert SQL, retry policy,
deduplication) sit in extractor.py / repository.py / api.py / dump.py and
do NOT depend on which source they're operating on.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    name: str  # short id, written into products.source
    batch_name: str  # batch_sync_log.batch_name (per-source cursor)
    api_base_url: str  # Search API root (no trailing slash)
    dump_url: str  # canonical .jsonl.gz dump (used by docs only)
    user_agent: str  # required by each Open*Facts project
    photo_hosts: frozenset[str] = field(default_factory=frozenset)
    # storage_type classification (frozen/fresh/ambient) is food-only by design
    # — the rules in `product_knowledge.json` target categories like "Surgelés"
    # or "Produits laitiers". For non-food catalogues (OBP cosmetics, OPF
    # generic, OPFF pet food) the classification result is meaningless, so we
    # skip it entirely and persist `storage_type = NULL`. Cf plan PR2 § DA-08
    # and ARCH_BATCH_OFF_SYNC.md.
    classify_storage: bool = False


_DEFAULT_UA = "Ratis/1.0 (contact: hike.muskox5137@eagereverest.com)"

SOURCES: dict[str, Source] = {
    "off": Source(
        name="off",
        batch_name="off_sync",
        api_base_url="https://world.openfoodfacts.org",
        dump_url="https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz",
        user_agent=_DEFAULT_UA,
        photo_hosts=frozenset(
            {
                "images.openfoodfacts.org",
                "static.openfoodfacts.org",
                "world.openfoodfacts.org",
            }
        ),
        classify_storage=True,
    ),
    "obp": Source(
        name="obp",
        batch_name="obp_sync",
        api_base_url="https://world.openbeautyfacts.org",
        dump_url="https://static.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz",
        user_agent=_DEFAULT_UA,
        photo_hosts=frozenset(
            {
                "images.openbeautyfacts.org",
                "static.openbeautyfacts.org",
                "world.openbeautyfacts.org",
            }
        ),
    ),
    "opf": Source(
        name="opf",
        batch_name="opf_sync",
        api_base_url="https://world.openproductsfacts.org",
        dump_url="https://static.openproductsfacts.org/data/openproductsfacts-products.jsonl.gz",
        user_agent=_DEFAULT_UA,
        photo_hosts=frozenset(
            {
                "images.openproductsfacts.org",
                "static.openproductsfacts.org",
                "world.openproductsfacts.org",
            }
        ),
    ),
    "opff": Source(
        name="opff",
        batch_name="opff_sync",
        api_base_url="https://world.openpetfoodfacts.org",
        dump_url="https://static.openpetfoodfacts.org/data/openpetfoodfacts-products.jsonl.gz",
        user_agent=_DEFAULT_UA,
        photo_hosts=frozenset(
            {
                "images.openpetfoodfacts.org",
                "static.openpetfoodfacts.org",
                "world.openpetfoodfacts.org",
            }
        ),
    ),
}


def get_source(name: str) -> Source:
    """Return the Source for `name`, or raise KeyError."""
    if name not in SOURCES:
        raise KeyError(f"Unknown source {name!r}. Known: {sorted(SOURCES)}.")
    return SOURCES[name]
