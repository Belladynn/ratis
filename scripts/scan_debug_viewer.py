"""Scan debug viewer — local HTML report from /admin/<scope>/<id>/debug.

Usage :
    python scripts/scan_debug_viewer.py <receipt_id>
    python scripts/scan_debug_viewer.py --scan <scan_id>

Reads ADMIN_API_KEY from one of (in priority order) :
    1. RATIS_ADMIN_API_KEY env var (export it in your shell)
    2. tools/.env.local (RATIS_ADMIN_API_KEY=... line)

Calls https://products.ratis.app/api/v1/admin/{scope}/{id}/debug, downloads
the processed images via the R2 presigned URLs returned, and generates a
self-contained HTML file (images embedded as base64) that opens in your
default browser.

Why this script vs a static HTML page :
    - No CORS issue (Caddy doesn't expose CORS headers on the admin
      endpoint, so a browser fetch from file:// or localhost would fail).
    - The Python process talks to Hetzner directly with the bearer token.
      The browser only opens the generated HTML, no API call from there.

Output : ``./.scan-debug-<id>.html`` (gitignored — see .gitignore).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROD_BASE = "https://products.ratis.app"
ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = ROOT / "tools" / ".env.local"


def load_admin_key() -> str:
    """Try env var first, then tools/.env.local. Fail fast with guidance."""
    key = os.environ.get("RATIS_ADMIN_API_KEY", "").strip()
    if key:
        return key
    if ENV_LOCAL.exists():
        load_dotenv(ENV_LOCAL)
        key = os.environ.get("RATIS_ADMIN_API_KEY", "").strip()
        if key:
            return key
    sys.exit(
        "RATIS_ADMIN_API_KEY missing.\n"
        "Either : export RATIS_ADMIN_API_KEY=<your-key>\n"
        f"Or : add RATIS_ADMIN_API_KEY=<your-key> to {ENV_LOCAL}"
    )


def fetch_debug(scope: str, id_: str, token: str) -> dict:
    """Call /api/v1/admin/{scope}/{id}/debug. scope ∈ {receipts, scans}."""
    url = f"{PROD_BASE}/api/v1/admin/{scope}/{id_}/debug"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
    if r.status_code == 404:
        sys.exit(f"404 — no debug data for {scope}/{id_}. (Was STORE_DEBUG=true at scan time ?)")
    if r.status_code in (401, 403):
        sys.exit(f"{r.status_code} — admin auth refused. Check RATIS_ADMIN_API_KEY value.")
    r.raise_for_status()
    return r.json()


def fetch_image_b64(presigned_url: str) -> str | None:
    """Download an R2 presigned URL and return base64 data URI."""
    if not presigned_url:
        return None
    r = httpx.get(presigned_url, timeout=30.0)
    r.raise_for_status()
    return f"data:image/jpeg;base64,{base64.b64encode(r.content).decode('ascii')}"


def render_html(payload: dict, images: dict[str, str | None]) -> str:
    """Return a single self-contained HTML string."""
    receipt_id = payload.get("receipt_id") or "unknown"
    scan_id = payload.get("scan_id") or "—"
    scan_status = payload.get("scan_status") or "—"
    scanned_at = payload.get("scanned_at") or "—"
    rich_blocks = payload.get("rich_blocks") or []
    llm_output = payload.get("llm_output")
    # Phase 2e (ARCH OCR↔LLM Bridge v2) : the old ``legacy_receipt_data``
    # field actually held the receipt_data USED to create the scan
    # (LLM-derived in most cases). It was renamed to
    # ``final_receipt_data``. ``legacy_parser_output`` is a NEW separate
    # field that captures the actual ``parse_receipt(ocr)`` result run
    # in parallel for true side-by-side comparison.
    final_receipt_data = payload.get("final_receipt_data") or payload.get("legacy_receipt_data")
    legacy_parser_output = payload.get("legacy_parser_output")
    passes_summary = payload.get("ocr_passes_summary") or {}
    scan_items = payload.get("scan_items") or []

    raw_url = payload.get("raw_image_url")

    # Image grid : raw + 4 passes side-by-side, with per-pass OCR text below
    image_cards = []
    raw_b64 = fetch_image_b64(raw_url) if raw_url else None
    image_cards.append(("raw", raw_b64, "—", []))
    # Per-pass `text_blocks` extracted from passes_summary so we can see what
    # each pass actually OCR'd (to explain anomalies like "44 blocks on a
    # near-black image" — usually noise).
    for name in ("corrected", "clahe", "binarized", "inverted"):
        b64 = images.get(name)
        meta = passes_summary.get(name) or {}
        # match_ratio added 2026-04-28 — fraction of winner OCR blocks
        # that this pass also recovered (fuzzy ratio ≥0.85). None when
        # missing (legacy debug rows pre-PR) or when no winner exists.
        match_ratio = meta.get("match_ratio") if meta else None
        if match_ratio is None:
            match_str = "match=N/A"
            match_class = ""
        else:
            pct = round(match_ratio * 100)
            match_str = f"match={pct}%"
            # Visual flag : <30% = catastrophic (red), 30-70% = orange,
            # ≥70% = green. Helps eyeball which pass is producing junk.
            if match_ratio < 0.30:
                match_class = "match-bad"
            elif match_ratio < 0.70:
                match_class = "match-warn"
            else:
                match_class = "match-good"
        meta_str = (
            f"n_blocks={meta.get('n_blocks', '?')}, time_ms={meta.get('time_ms', '?')}, "
            f"<span class='{match_class}'>{match_str}</span>"
            if meta
            else "—"
        )
        text_blocks = meta.get("text_blocks", []) if meta else []
        image_cards.append((name, b64, meta_str, text_blocks))

    image_cards_html = ""
    for name, b64, meta, text_blocks in image_cards:
        # Render OCR text under the image — escape HTML, join with <br>.
        # Truncate per line to 60 chars so layout stays readable.
        text_html = ""
        if text_blocks:
            escaped = [
                (t[:60] + "…" if len(t) > 60 else t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                for t in text_blocks
            ]
            text_html = (
                "<details class='ocr-text'>"
                f"<summary>OCR text ({len(text_blocks)} lines)</summary>"
                f"<pre>{chr(10).join(escaped)}</pre>"
                "</details>"
            )
        if b64:
            image_cards_html += f"""
            <div class="card">
                <div class="card-h">{name} <span class="meta">{meta}</span></div>
                <img src="{b64}" alt="{name}"/>
                {text_html}
            </div>"""
        else:
            image_cards_html += f"""
            <div class="card empty">
                <div class="card-h">{name}</div>
                <div class="empty-msg">not stored / not triggered</div>
                {text_html}
            </div>"""

    rich_blocks_html = "<table><tr><th>#</th><th>text</th><th>conf</th><th>x</th><th>y</th><th>w</th><th>h</th></tr>"
    for i, b in enumerate(rich_blocks):
        text = (b.get("text") or "").replace("<", "&lt;").replace(">", "&gt;")
        rich_blocks_html += (
            f"<tr><td>{i}</td><td>{text}</td>"
            f"<td>{b.get('confidence', '?')}</td>"
            f"<td>{b.get('x', '?')}</td><td>{b.get('y', '?')}</td>"
            f"<td>{b.get('w', '?')}</td><td>{b.get('h', '?')}</td></tr>"
        )
    rich_blocks_html += "</table>"

    llm_pretty = json.dumps(llm_output, indent=2, ensure_ascii=False) if llm_output else "<none>"
    final_pretty = json.dumps(final_receipt_data, indent=2, ensure_ascii=False) if final_receipt_data else "<none>"
    legacy_parser_pretty = (
        json.dumps(legacy_parser_output, indent=2, ensure_ascii=False) if legacy_parser_output else "<none>"
    )
    items_pretty = json.dumps(scan_items, indent=2, ensure_ascii=False) if scan_items else "<none>"

    return f"""<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="utf-8"/>
<title>scan_debug — {receipt_id}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 16px; background: #1a1a1a; color: #ddd;
  }}
  h1 {{ font-size: 16px; margin: 8px 0; }}
  h2 {{
    font-size: 14px; margin: 24px 0 8px; color: #6cf;
    border-bottom: 1px solid #333; padding-bottom: 4px;
  }}
  .ids {{ color: #888; font-size: 12px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
  .card {{ background: #222; border: 1px solid #333; border-radius: 4px; padding: 8px; }}
  .card.empty {{ opacity: 0.5; }}
  .card-h {{ font-size: 13px; font-weight: 600; margin-bottom: 6px; color: #fff; }}
  .card-h .meta {{ color: #888; font-weight: normal; font-size: 11px; margin-left: 6px; }}
  .match-bad {{ color: #ff5a5a; font-weight: 600; }}
  .match-warn {{ color: #ffa64d; font-weight: 600; }}
  .match-good {{ color: #6cf08a; font-weight: 600; }}
  .empty-msg {{ color: #888; padding: 60px 0; text-align: center; }}
  .ocr-text {{ margin-top: 6px; font-size: 11px; }}
  .ocr-text summary {{ cursor: pointer; color: #6cf; user-select: none; }}
  .ocr-text pre {{ font-size: 10px; max-height: 280px; overflow-y: auto;
                   line-height: 1.3; padding: 6px; margin: 4px 0 0 0; }}
  img {{ max-width: 100%; height: auto; display: block; border: 1px solid #333; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th, td {{ border: 1px solid #333; padding: 4px 8px; text-align: left; vertical-align: top; }}
  th {{ background: #2a2a2a; color: #6cf; }}
  pre {{ background: #111; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 12px; line-height: 1.5; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
</style>
</head><body>

<h1>scan_debug — {receipt_id}</h1>
<div class="ids">
    scan_id: {scan_id} ·
    scan_status: {scan_status} ·
    scanned_at: {scanned_at}
</div>

<h2>Images (raw + processed passes)</h2>
<div class="grid">{image_cards_html}</div>

<h2>OCR passes summary</h2>
<pre>{json.dumps(passes_summary, indent=2)}</pre>

<h2>Rich blocks (winning pass — post-arbitration)</h2>
<details><summary>Show {len(rich_blocks)} blocks</summary>
{rich_blocks_html}
</details>

<h2>LLM output (3-bucket schema)</h2>
<pre>{llm_pretty}</pre>

<h2>Final receipt data — Legacy parser (parallel run)</h2>
<div class="grid-2">
<div><h3 style="margin: 0; font-size: 12px;">Final receipt data (used for scan)</h3><pre>{final_pretty}</pre></div>
<div><h3 style="margin: 0; font-size: 12px;">Legacy parser output (parallel)</h3><pre>{legacy_parser_pretty}</pre></div>
</div>

<h2>Scan items (persisted in DB)</h2>
<pre>{items_pretty}</pre>

</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id", help="receipt_id (default scope) or use --scan for scan_id")
    parser.add_argument("--scan", action="store_true", help="treat <id> as a scan_id (legacy endpoint)")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open browser")
    args = parser.parse_args()

    token = load_admin_key()
    scope = "scans" if args.scan else "receipts"

    # ASCII-only output : Windows cp1252 console crashes on Unicode arrows
    # (lesson 2026-04-27 — `→` raised UnicodeEncodeError mid-pipeline).
    print(f"-> fetching /api/v1/admin/{scope}/{args.id}/debug ...")
    payload = fetch_debug(scope, args.id, token)

    print("-> downloading processed images via R2 presigned URLs ...")
    images = {}
    for name, url in (payload.get("processed_images") or {}).items():
        if url:
            print(f"   - {name}")
            images[name] = fetch_image_b64(url)
    # Legacy single-image fallback for pre-PR-132 rows
    if not images and payload.get("processed_image_url"):
        print("   - corrected (legacy)")
        images["corrected"] = fetch_image_b64(payload["processed_image_url"])

    out_path = ROOT / f".scan-debug-{args.id}.html"
    out_path.write_text(render_html(payload, images), encoding="utf-8")
    print(f"-> wrote {out_path}")

    if not args.no_open:
        webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
