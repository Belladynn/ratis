# Receipts test fixtures

Real-world OCR blocks captured from PaddleOCR on actual receipt photos,
paired with the expected parsed output (items + totals + store + date).

Used by `tests/test_llm_filter.py` to iterate on the LLM prompt without
hitting the Mistral API on every run.

## File pairs

Each receipt fixture is a pair :

```
receipts/
  vente_a_emporter_le_v.ocr.json      # PaddleOCR raw blocks (rich format)
  vente_a_emporter_le_v.expected.json # what the LLM should output

  monoprix_001.ocr.json
  monoprix_001.expected.json

  carrefour_001.ocr.json
  carrefour_001.expected.json

  ...
```

## OCR blocks file format (.ocr.json)

```json
{
  "source": "synthetic" | "captured",
  "source_id": "<receipt UUID if captured from prod, descriptive name if synthetic>",
  "image_dimensions": [width_px, height_px],
  "blocks": [
    {"text": "MONOPRIX", "confidence": 0.99, "x": 250.0, "y": 30.0, "w": 180.0, "h": 28.0},
    {"text": "Pâtes 500g", "confidence": 0.95, "x": 60.0, "y": 200.0, "w": 220.0, "h": 18.0},
    ...
  ]
}
```

`x, y` are the **center** of the block. `w, h` are width/height. Same
shape as `RichOcrBlock.to_dict()` in `worker/pipeline/ocr_engine.py`.

## Expected file format (.expected.json)

```json
{
  "store_name": "Monoprix" | null,
  "store_status_hint": "confirmed" | "pending" | "unknown" | null,
  "purchased_at": "2026-04-22" | null,
  "items": [
    {"scanned_name": "Pâtes 500g", "price_cents": 250, "quantity": 1.0}
  ],
  "total_cents": 1876,
  "tva_cents": 96,
  "rejections": [
    "CARTE TRD CB 18,76",
    "ESPECES 0,00",
    "A5,50%"
  ]
}
```

`rejections` lists OCR blocks that the LLM **must not** treat as items
(payment methods, TVA codes, totals, fidelity numbers, header/footer).
Useful as anti-cases when grading the LLM output.

## Capturing real fixtures from prod

When the rich-blocks logging deploys, real receipts in prod log JSON to
the worker logs. Pull one with :

```bash
ssh root@<prod-vm> "docker compose ... logs --tail=500 product_analyser_worker \
  | grep 'ocr.rich_blocks' | tail -1"
```

Strip the log prefix, save the JSON array as the `blocks` field of a
new `<name>.ocr.json` file. Hand-write the corresponding `.expected.json`
based on the actual receipt photo.

## Synthetic fixtures

The first fixture (`vente_a_emporter_le_v_001`) is hand-crafted from the
data we observed on a real Le V de la Vérité receipt during alpha-day
2026-04-26 (cf SESSION_LOG). It reproduces the typical artifacts :
- Header lines (date+heure+barcode collés, restaurant name)
- Body with item names + prices
- Footer (RESTE A PAYER, TVA, mode paiement)

Lets us iterate on the LLM prompt before real captures land.
