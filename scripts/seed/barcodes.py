"""Barcode HTML generator (Step 2-bis).

Renders one HTML page with one EAN-13 SVG barcode per seeded product +
1 synthetic invalid EAN at the bottom for the rejection-test workflow.

Workflow
========
Open ``docs/seed/barcodes.html`` on a second screen, point the dev mobile
app at it, scan barcodes one by one — exercises the full ``/scan`` pipeline
against products that are actually in ``ratis_seed``.

CLI
===
::

    python -m scripts.seed.barcodes [output_path]

Default output path is ``docs/seed/barcodes.html`` (relative to repo root).

DRY
===
The EAN list is imported directly from
:data:`scripts.seed.products.SEED_PRODUCTS` (+ :data:`INVALID_PRODUCT`) — no
duplication, no drift risk. When products evolve, regenerate via
``make seed-barcodes``.

Dependency
==========
``python-barcode`` (dependency group ``seed`` in root ``pyproject.toml``).
Not pulled in by production service images.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path

from barcode import EAN13

from scripts.seed.products import INVALID_PRODUCT, SEED_PRODUCTS, SeedProduct

# Default output path — relative to repo root (resolved at runtime).
_DEFAULT_OUTPUT = Path("docs/seed/barcodes.html")


def _render_one_barcode_svg(ean: str) -> str:
    """Return the inline SVG markup for one EAN-13 code.

    ``no_checksum=True`` keeps the literal EAN we provided (real OFF EANs
    already include the correct GS1 check digit ; the synthetic
    ``9999999999999`` intentionally does not — letting the barcode render
    anyway is the point of the rejection-test entry).
    """
    code = EAN13(ean, no_checksum=True)
    buf = io.BytesIO()
    # ``write_text=False`` — we render the label ourselves in HTML for
    # consistent typography across categories.
    code.write(buf, options={"write_text": False, "module_height": 12.0})
    svg = buf.getvalue().decode("utf-8")
    # Strip the XML declaration so the SVG inlines cleanly inside HTML.
    if svg.startswith("<?xml"):
        svg = svg.split("?>", 1)[1].lstrip()
    return svg


def _render_card(product: SeedProduct, *, invalid: bool = False) -> str:
    """Render one product card (barcode SVG + label) as HTML."""
    svg = _render_one_barcode_svg(product["ean"])
    badge = (
        '<span class="badge invalid">INVALIDE</span>'
        if invalid
        else f'<span class="badge">{product["category"]}</span>'
    )
    return f"""
      <div class="card{" card-invalid" if invalid else ""}">
        <div class="barcode">{svg}</div>
        <div class="ean">{product["ean"]}</div>
        <div class="name">{product["name"]}</div>
        {badge}
      </div>
    """


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <title>Ratis — Barcodes seed personas</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f7f7f8;
      margin: 0;
      padding: 24px;
      color: #1f2937;
    }}
    h1 {{ font-size: 22px; margin: 0 0 4px; }}
    h2 {{ font-size: 14px; color: #6b7280; font-weight: 500; margin: 0 0 24px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }}
    .card {{
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 16px;
      text-align: center;
    }}
    .card-invalid {{ border-color: #f87171; background: #fef2f2; }}
    .barcode svg {{ max-width: 100%; height: auto; }}
    .ean {{
      font-family: 'SF Mono', Menlo, monospace;
      font-size: 12px;
      color: #6b7280;
      margin-top: 6px;
    }}
    .name {{
      font-weight: 600;
      font-size: 13px;
      margin-top: 4px;
      line-height: 1.3;
    }}
    .badge {{
      display: inline-block;
      margin-top: 8px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      background: #eef2ff;
      color: #4338ca;
    }}
    .badge.invalid {{ background: #fee2e2; color: #b91c1c; font-weight: 600; }}
    footer {{
      margin-top: 32px;
      font-size: 11px;
      color: #9ca3af;
      text-align: center;
    }}
  </style>
</head>
<body>
  <h1>Ratis — Barcodes seed personas</h1>
  <h2>
    {n_valid} produits seedés + 1 EAN invalide synthetic — généré le {gen_iso}
    · ouvrir sur 2e écran, scanner avec l'app mobile (dev mode)
  </h2>

  <div class="grid">
    {cards}
  </div>

  <footer>
    Régénérer après modification de <code>scripts/seed/products.py</code> :
    <code>make seed-barcodes</code> → <code>docs/seed/barcodes.html</code>.
    Voir <code>ARCH_seed_test_data.md</code> § Step 2-bis.
  </footer>
</body>
</html>
"""


def generate_barcodes_html(output_path: Path | None = None) -> Path:
    """Generate the barcodes HTML page and write it to ``output_path``.

    ``output_path`` defaults to ``docs/seed/barcodes.html`` resolved against
    the repo root (parent of this module's grandparent). Creates the parent
    directory if it doesn't already exist. Returns the resolved output path.
    """
    if output_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        output_path = repo_root / _DEFAULT_OUTPUT
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cards = [_render_card(p) for p in SEED_PRODUCTS]
    cards.append(_render_card(INVALID_PRODUCT, invalid=True))

    html = _HTML_TEMPLATE.format(
        n_valid=len(SEED_PRODUCTS),
        gen_iso=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        cards="\n".join(cards),
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> int:
    """CLI entry point — ``python -m scripts.seed.barcodes [output_path]``."""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    written_to = generate_barcodes_html(output)
    print(f"[seed-barcodes] wrote {len(SEED_PRODUCTS) + 1} barcodes to {written_to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
