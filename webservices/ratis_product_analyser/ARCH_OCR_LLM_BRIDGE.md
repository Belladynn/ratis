---
type: sub-arch
service: ratis_product_analyser
parent: ARCH_PRODUCT_ANALYSER
related: [ARCH_PRODUCT_ANALYSER, TRAINING]
status: design
tags: [ocr, llm, fuzzy-match, knowledge-loop, paddleocr, training, local-first, denoise-only, cluster-by-bbox]
updated: 2026-04-28
---

# ratis_product_analyser — OCR ↔ LLM Bridge (local-first, denoise-only LLM)

> OCR ↔ LLM pipeline **design phase v2**: local-first EAN matching (PaddleOCR + fuzzy), LLM downgraded to pure-denoise + classify residue cluster-by-bbox. No DB candidates as LLM input. Feedback loop via `ocr_knowledge`.
> @tags: ocr llm paddleocr fuzzy-match knowledge-loop training local-first denoise-only cluster-by-bbox anti-hallucination design ocr_knowledge
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_PRODUCT_ANALYSER]] · Relations : [[TRAINING]]

> Status: 📐 Design phase. Draft co-written with user 2026-04-28.
> **v2** (PM 2026-04-28): LLM downgraded to pure-denoise + classify. No more
> EAN matching on the LLM side, no more DB candidates as input. Matching
> remains 100% local fuzzy (Stage 1d or post-LLM on denoised text).
> Branch: `main` (impl to be dispatched post-ARCH validation)

---

## Index

- [Problem](#problem)
- [Guiding principle — local-first + LLM denoise-only](#guiding-principle--local-first--llm-denoise-only)
- [Overview](#overview)
- [Stage 1 — Local pre-filter](#stage-1--local-pre-filter)
- [Stage 2 — LLM denoise + classify (residue, cluster-based)](#stage-2--llm-denoise--classify-residue-cluster-based)
- [Cluster-by-bbox](#cluster-by-bbox)
- [LLM prompt (full English template)](#llm-prompt-full-english-template)
- [LLM output schema](#llm-output-schema)
- [Similarity guard (anti-hallucination)](#similarity-guard-anti-hallucination)
- [Internationalization](#internationalization)
- [Cache + feedback loop ocr_knowledge](#cache--feedback-loop-ocr_knowledge)
- [Acted decisions](#acted-decisions)
- [Pending decisions](#pending-decisions)
- [Implementation checklist](#implementation-checklist)
- [Tests](#tests)
- [Glossary](#glossary)

---

## Problem

Direct OCR-output ↔ canonical product matching (1M+ OFF entries) via `pg_trgm` fails in most real-world cases:

- **Semantic gap**: the cashier line "HIPROA RRE SAV FRSE" and the OFF marketing name "Yaourt à boire fraise framboise" share almost no 3+-character substrings. `word_similarity` returns 0.
- **Systematic OCR errors**: character confusions (R↔B, A↔H, O↔0, I↔1), token fusions (HIPRO+A → HIPROA, space lost due to hard thresholding), character drops (morphological over-processing).
- **Cashier abbreviations**: receipts use BRE=brassé, SAV=saveur, FRSE=fraise, etc.
- **Inconsistent OFF brand fields**: "Danone", "HiPRO", "Danone, Hipro, Hipro drinks" for the same commercial brand.

**Alpha observation 2026-04-28 (v1 LLM-as-matcher)**: the LLM hallucinated `scanned_name="COUS COUS"` from a clear OCR reading `UCHA KOMBUCHA ANAN`, because DB candidates did not match and it "filled the gap" with a plausible-but-wrong product. Consequence: fuzzy match promoted COUS COUS → EAN Trevijano; false match persisted to DB. See scan `a61d2f67-c3fd-4e2e-aa96-309a352ee2c0` (manually rejected).

**Conclusion**: the LLM has no business proposing an EAN. Its only added value is to **denoise the OCR text** (fix fusions, character confusions) by reconciling the multiple pre-processing passes. Matching remains local and deterministic.

**We want**:
1. **`corrected_text`** = what is printed on the receipt, denoised from the 3 preprocessing variants, **without hallucination**.
2. **`matched_ean`** (resolved locally by fuzzy `products.name`, after validated denoise) = OFF reference for cashback.
3. **Guard**: if the LLM strays too far from the raw OCR, reject its output and keep the raw.

---

## Guiding principle — local-first + LLM denoise-only

**The LLM is a character-level OCR corrector, not a product matcher.**

For each scan:
- **Stage 1** (always, no LLM) attempts to classify each block via cache + local DB lookups. Every resolved block = 0 LLM calls.
- **Stage 2** (residue only) calls the LLM **solely for denoise + classify** on unresolved clusters. No EAN, no candidates as input.
- **Similarity guard** (post-LLM) compares the output to the raw OCR. If too divergent → reject LLM, keep raw.
- **EAN matching** = local fuzzy (`match_product` on `products.name`/`brands`). Always. Without exception.

**Benefits vs v1 (LLM-as-matcher):**

| Aspect | v1 (LLM matches EAN) | v2 (LLM denoise only) |
|---|---|---|
| **Product hallucination** | Possible (see COUS COUS) | Blocked by similarity guard |
| **Cross-pass consistency** | LLM sees only the winner | LLM sees 3 variants → better reconciliation |
| **LLM cost** | Input enriched with DB candidates | Input enriched with variants (equivalent) |
| **Output schema** | scanned_name + corrected + matched_ean + match_confidence | corrected_text + classification + (optional dismissal_category) |
| **Separation of concerns** | LLM = matching + denoise (mixed) | LLM = denoise+classify; local fuzzy = matching |
| **Robustness** | LLM down → no output | LLM down → fallback raw OCR (graceful) |

**Self-reinforcing preserved**: `ocr_knowledge` continues to learn from validated LLM outputs (post-guard) → cache converges → fewer blocks sent to LLM next time.

---

## Overview

```
                    ┌──────────────────┐
                    │ OCR rich_blocks  │  (3 passes : corrected, clahe,
                    │ par pass         │   binarized)
                    └────────┬─────────┘
                             │
              ┌──────────────▼────────────────────────┐
              │ Stage 1 — Local pre-filter (zéro LLM) │
              │   1a. dismissal lookup                │
              │   1b. product full-seq cache lookup   │
              │   1c. token cleanup + retry           │
              │   1d. match_product fuzzy             │
              │   1e. unclassified → résidu           │
              └──────────────┬────────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ Résidu vide ?        │
                  └──┬───────────────┬───┘
                     │ OUI           │ NON
                     ▼               ▼
              ┌────────────┐  ┌────────────────────────────────┐
              │ DONE       │  │ Stage 2 — LLM denoise+classify │
              │ no LLM     │  │ (résidu only)                  │
              │ cost=0     │  │                                │
              └────────────┘  │ 2a. cluster-by-bbox            │
                              │     (groupe variants 3 passes  │
                              │     au même y±tolerance)       │
                              │                                │
                              │ 2b. LLM call                   │
                              │     INPUT  : clusters + country│
                              │     OUTPUT : per-cluster       │
                              │              corrected_text +  │
                              │              classification    │
                              │                                │
                              │ 2c. Similarity guard           │
                              │     for each cluster:          │
                              │       ratio = similarity(      │
                              │         llm_text, raw_winner)  │
                              │       if ratio < 0.6 :         │
                              │         REJECT → use raw       │
                              │       if ratio >= 0.6 :        │
                              │         ACCEPT → use llm_text  │
                              │                                │
                              │ 2d. ocr_knowledge upsert       │
                              │     (only on accepted denoise) │
                              │                                │
                              │ 2e. local fuzzy match per      │
                              │     accepted product cluster   │
                              │     → product_ean              │
                              └────────────────┬───────────────┘
                                               │
                                               ▼
                              ┌─────────────────────────────────┐
                              │ scans.persist                   │
                              │   scanned_name = raw OCR        │
                              │   corrected_name = LLM denoised │
                              │       (or raw if guard rejected)│
                              │   product_ean = local fuzzy     │
                              │       (None if no match)        │
                              └─────────────────────────────────┘
```

---

## Stage 1 — Local pre-filter

(Unchanged vs v1 — see initial ARCH. Summary: 1a dismissal cache → 1b product full-seq cache → 1c token cleanup → 1d match_product fuzzy → 1e residue.)

Implementation: `worker/pipeline/local_prefilter.py:local_classify_blocks`. Tests: `tests/test_local_prefilter.py`. See PR #163.

---

## Stage 2 — LLM denoise + classify (residue, cluster-based)

In `worker/pipeline/llm_orchestrator.py` (refacto) + `worker/pipeline/llm_filter.py` (prompt extension).

**LLM input**: for each block in the Stage 1 residue, we group the 3 OCR variants (1 per pass) at the same bbox via `cluster_by_bbox`, then send this list of clusters to the LLM along with the country.

**LLM output**: for each cluster, a classification (`product` / `dismissal` / `total` / `retailer` / `other`) + a `corrected_text` (denoised) or `null` (LLM unable to reconcile).

**Not in the output**:
- ❌ `matched_ean` — the LLM does not propose an EAN
- ❌ `match_confidence` — replaced by classification + similarity guard on the worker side
- ❌ DB candidates as input — strict separation

---

## Cluster-by-bbox

In `worker/pipeline/cluster_blocks.py` (new file).

**Function**: `cluster_blocks_by_bbox(passes_results, y_tolerance=10) -> list[Cluster]`

Algorithm:
1. For each pass, we have a list of `RichOcrBlock` with `(text, x, y, w, h, confidence)`
2. **Seed clusters** from the winner pass (corrected generally) — each winner block = 1 cluster
3. **Attach** blocks from other passes to existing clusters: for each non-winner block, find the nearest cluster in y (tolerance ±10px by default). If no cluster < tolerance → ignore (isolated block, probably noise).
4. Result: list of `Cluster(id, y, variants=[(pass_name, text, confidence), ...])`

**Why y only?** Receipts are narrow columns. The x position varies little between passes (only small offsets due to preprocessing). y is the main discriminant for identifying "same receipt line".

**Tolerance ±10px**: empirical. If too strict (e.g. ±2px), passes with slight offsets will miss the cluster. If too wide (e.g. ±50px), 2 adjacent lines may merge. To calibrate in alpha if needed.

**Edge case — block present in only 1 pass**: if a block appears in corrected but not the other 2 (or vice versa), we keep the cluster with a single variant. The LLM receives this information ("only 1 of 3 passes saw this") and can decide accordingly.

---

## LLM prompt (full English template)

The prompt is in English (LLM standard), even if receipts are localized. The `country` param is passed so the LLM recognizes local boilerplate.

```
SYSTEM
You are an OCR post-processor for retail receipts. You receive
"clusters" — each cluster is multiple OCR readings of the same
physical text block, produced by N image-preprocessing variants
(corrected / clahe / binarized).

CONTEXT
- country         : {country_code}             # e.g. "FR", "DE", "ES"
- receipt_locale  : {receipt_locale}           # e.g. "fr-FR"
- ocr_engine      : PaddleOCR
- preprocessing   : 3 variants per block (may be fewer per cluster
                    if a pass missed the block)

YOUR JOB
For each cluster, do TWO things in one go:
  (1) CLASSIFY the cluster as one of:
        - product      → an item the customer bought
        - dismissal    → boilerplate (greeting, footer, totals header,
                         legal notice, payment line, TVA breakdown, …)
        - total        → final receipt total amount line
        - retailer     → store name, address, SIRET / tax ID
        - other        → anything else (skipped downstream)
  (2) If `product` : produce `corrected_text` by reconciling variants.
      For non-product : copy the cleanest variant verbatim into
      `corrected_text`.

YOUR ONLY ROLE = DENOISE OCR
You correct character-level OCR errors where variants disagree:
spaces, O↔0, I↔1, B↔R, A↔H, S↔5, dropped chars at edges.
You DO NOT invent words. You DO NOT substitute brand names. You
DO NOT fill blanks with placeholders like "PRODUCT" or "PRODUIT".

THINK LIKE AN OCR
Realistic OCR errors come from image artifacts, NOT semantic guesses:
- binarized (hard threshold + adaptive) does TWO classes of damage:
  (a) FUSES adjacent characters when whitespace is too narrow,
      e.g. "UCHA KOMBUCHA" → "UCHAKOMBUCHA"
  (b) FRAGMENTS thin strokes, leaking shape confusions O↔0
      ("KOMBUCHA"→"K0MBUCHA"), I↔1, B↔R, A↔H, S↔5
- clahe (contrast-limited histogram equalization) is generally
  clean. On strongly uneven light it may amplify local noise that
  can mimic binarized-style fragmentations, but in practice rarely.
- corrected (deskew + brightness normalization) is the baseline.
  Cleanest of the 3 in most cases. Can still drop or duplicate
  low-confidence chars at edges (paper folds, blur).
NEVER A REAL OCR ERROR: substituting a whole word with a different
word that means something else. "UCHA KOMBUCHA" → "COUS COUS" is
linguistically impossible from OCR alone — that would be an LLM
hallucination, which is exactly what you must avoid.

REASONING STEPS PER CLUSTER

  Step 1 — Identify the majority shape.
           Count which character sequence appears most often.
           Rank variants by similarity to each other.

  Step 2 — Pick majority reading or fuse character-level variants.
           If 3+ variants agree exactly → use that text.
           If 2 disagree on chars only (O↔0, missing space) → choose
           the variant matching natural language conventions:
             "K0MBUCHA" + "KOMBUCHA" → "KOMBUCHA"
               (O is the natural char, 0 is OCR misread)
             "UCHAKOMBUCHA" + "UCHA KOMBUCHA" → "UCHA KOMBUCHA"
               (spaces are rarely INSERTED by OCR ; binarized DROPS them)

  Step 3 — If you cannot reconcile (variants too divergent OR no
           majority OR all variants are gibberish) → return null.
           Better to say "I don't know" than to guess. The downstream
           pipeline keeps the raw OCR text in this case.

  Step 4 — Classify : after denoising, decide the type.
           Receipts in {country} typically have:
             - greetings (BONJOUR, MERCI, BIENVENUE, A BIENTOT for FR ;
               WELCOME, THANK YOU for EN ; …)
             - totals (TOTAL, MONTANT, T.T.C., RESTE A PAYER for FR)
             - tax breakdown (TVA, T.V.A., HT, TTC, with %)
             - payment (CB, ESPECES, RENDU, PAIEMENT)
             - footer legal (CONSERVER, ECHANGE, REPRISE, AVOIR)
             - retailer header (store name, address, SIRET 14 digits
               for FR, USt-IdNr for DE, NIF for ES, …)
           These are `dismissal` (or `total` / `retailer` for the
           specific cases above). Everything else with a price next
           to it is likely a `product`.

EXAMPLES OF CORRECT REASONING

  Example A — Product, clean majority
    Variants:
      corrected : "UCHA KOMBUCHA ANAN"   (conf 0.88)
      clahe     : "UCHA KOMBUCHA ANAN"   (conf 0.85)
      binarized : "UCHA K0MBUCHAANAN"    (conf 0.79)
    Reasoning:
      - corrected and clahe agree exactly → strong majority
      - binarized has TWO typical hard-threshold artifacts at once :
        (a) fused space between KOMBUCHA and ANAN
        (b) O↔0 misread on K0MBUCHA (thin top of O fragmented)
      - All variants describe the SAME 3-token sequence with various
        OCR damages
    Decision:
      classification    = "product"
      corrected_text    = "UCHA KOMBUCHA ANAN"

  Example B — Dismissal, ignore minor noise
    Variants:
      corrected : "MERCI DE VOTRE VISITE"
      clahe     : "MERCI DE VOTRE VISITE"
      binarized : "MERCI DE VOTRE VlSITE"      (l→I confusion)
    Decision:
      classification     = "dismissal"
      corrected_text     = "MERCI DE VOTRE VISITE"
      dismissal_category = "footer_thanks"

  Example C — Tax line
    Variants:
      corrected : "TVA 5,5%       10,49    0,58    11,07"
      clahe     : "TVA 5.5%       10,49    0,58    11,07"
    Decision:
      classification     = "dismissal"
      corrected_text     = "TVA 5,5% 10,49 0,58 11,07"
      dismissal_category = "tax_breakdown"

  Example D — Cannot reconcile, return null
    Variants:
      corrected : "?$%@@#"
      clahe     : "$%#@!"
      binarized : "..."
    Reasoning: no readable text in any variant. Don't guess.
    Decision:
      classification = "other"
      corrected_text = null

EXAMPLES OF INCORRECT REASONING (NEVER DO THIS)

  Anti-example A — World-knowledge substitution
    Variants : "UCHA KOMBUCHA ANAN" (×3 majority)
    WRONG reasoning : "I don't know a brand 'Ucha Kombucha', but
                      'Ciao Kombucha' is well-known. The OCR probably
                      misread 'CIAO' as 'UCHA' (C→U, I→C, A→H, O→A).
                      Output 'CIAO KOMBUCHA ANAN'."
    Why wrong : Those substitutions are NOT plausible OCR errors.
                C↔U is rare. I↔C never happens. You substituted a
                whole word based on what you believe the brand
                "should" be. That is hallucination.
    CORRECT  : "UCHA KOMBUCHA ANAN" — preserve what the OCR sees.
               The actual brand might be "Lucha" with L cut off, or
               genuinely "Ucha", or anything else. Not your problem
               to identify the brand. Your job is to denoise.

  Anti-example B — Filling blanks with plausible words
    Variants : "RBICCITRON" (×3, all variants identical)
    WRONG reasoning : "RBICCITRON is not a real word. Probably a
                      juice flavor : 'CITRON'. Output 'CITRON'."
    Why wrong : You are inventing. The OCR consistently sees
                "RBICCITRON" — that's the data. Maybe it's a brand
                name truncated, maybe a typo on the receipt itself.
                Not your problem.
    CORRECT  : Output "RBICCITRON" verbatim (all variants agree,
               high confidence in the read), classification likely
               "product" if a price is adjacent.

  Anti-example C — Placeholder filler when stuck
    Variants : disagreeing gibberish
    WRONG    : Output "PRODUCT" / "PRODUIT" / "ITEM" as a fallback
               name.
    Why wrong : Placeholders pollute the downstream cache. They lie
                about what's on the ticket.
    CORRECT  : Return null. The pipeline handles null cleanly.

  Anti-example D — Substituting product based on inferred match
    (Note: this prompt does NOT receive product candidates. The
    match against retailer DB happens AFTER you, in a local fuzzy
    step. Do NOT pre-empt that step by guessing what product the
    user bought. Output the cleanest reading of the OCR text.)

OUTPUT (strict JSON, schema validated downstream)

{
  "results": [
    {
      "id": "<cluster_id>",
      "classification": "product" | "dismissal" | "total" | "retailer" | "other",
      "corrected_text": "<string>" | null,
      "dismissal_category": "<string>" | null,    // only when classification = "dismissal"
      "rationale": "<one short sentence — for audit/debug>"
    }
  ]
}
```

**Prompt note**: kept in English for 3 reasons:
1. LLMs are trained predominantly on English → better instruction-following
2. The `country` param is sufficient to contextualize local boilerplate
3. Internationalization: changing the country = just changing the param, no prompt refactor needed

---

## LLM output schema

Extension of the `LlmReceiptOutput` schema in `worker/pipeline/llm_filter.py`:

```python
class ClusterResult(TypedDict):
    id: str                                    # cluster_id matching input
    classification: Literal[
        "product", "dismissal", "total", "retailer", "other"
    ]
    corrected_text: Optional[str]              # None = LLM unable to reconcile
    dismissal_category: Optional[str]          # only when classification="dismissal"
    rationale: str                             # short audit string

class LlmDenoiseOutput(TypedDict):
    results: list[ClusterResult]
```

**dismissal_category enum** (extensible):
- `greeting` (BONJOUR, BIENVENUE, WELCOME, ...)
- `footer_thanks` (MERCI DE VOTRE VISITE, A BIENTOT, ...)
- `tax_breakdown` (TVA, T.V.A., HT, TTC, %)
- `payment_method` (CB, ESPECES, RENDU, PAIEMENT, CARTE)
- `legal_notice` (CONSERVER, ECHANGE, REPRISE, AVOIR, GARANTIE)
- `retailer_header_attempt` (lines near the header that are not the retailer name itself — classified `retailer` separately)
- `other` (unknown category but clearly non-product)

**Backward compat** (transition v1 → v2): worker accepts both schemas for 1-2 alpha cycles. If output has `products[]` (v1 schema), worker falls back to old logic. If output has `results[]` (v2 schema), worker applies the new flow + similarity guard.

---

## Similarity guard (anti-hallucination)

In `worker/receipt_task.py` after the LLM call, **before** using the LLM `corrected_text`.

**Function**: `validate_llm_corrected(raw: str, corrected: str, threshold: float = 0.6) -> bool`

```python
from difflib import SequenceMatcher

def validate_llm_corrected(raw: str, corrected: str, threshold: float = 0.6) -> bool:
    """Return True if the LLM denoise stays close enough to the raw OCR.
    
    Uses SequenceMatcher.ratio() (case-insensitive) on the two strings.
    If ratio < threshold → likely hallucination, reject.
    """
    if not corrected or not raw:
        return False
    return SequenceMatcher(None, raw.upper(), corrected.upper()).ratio() >= threshold
```

**Applied per cluster**:

```python
for result in llm_output.results:
    cluster = clusters_by_id[result.id]
    raw_winner = cluster.variants[0].text  # corrected pass = winner by default
    
    if result.corrected_text is None:
        scanned_name = raw_winner          # LLM unable → keep raw
        corrected_name = None
        log("llm.cluster_unresolved", id=result.id, raw=raw_winner)
    elif not validate_llm_corrected(raw_winner, result.corrected_text):
        scanned_name = raw_winner          # hallucination → REJECT, keep raw
        corrected_name = None
        log("llm.rejected_hallucination", id=result.id,
            raw=raw_winner, hallucinated=result.corrected_text,
            ratio=SequenceMatcher(None, raw_winner.upper(),
                                  result.corrected_text.upper()).ratio())
    else:
        scanned_name = raw_winner          # raw OCR always preserved as scanned_name
        corrected_name = result.corrected_text  # accepted denoise → use as display
        log("llm.accepted_denoise", id=result.id, ratio=ratio)
```

**Threshold 0.6**:
- "UCHA KOMBUCHA ANAN" vs "COUS COUS" → 0.10 ❌ rejected
- "UCHAKOMBUCHAANAN" vs "UCHA KOMBUCHA ANAN" → 0.85 ✅ accepted
- "UCHA KOMBUCHA AANAN" vs "UCHA KOMBUCHA ANAN" → 0.94 ✅ accepted
- "RBICCITRON" vs "CITRON" → 0.50 ❌ rejected (suspicious substitution)

Adjustable via `ratis_settings.json` § `llm.similarity_guard_threshold`. V0 = 0.6. To recalibrate if too many legitimate rejects or too many hallucinations getting through.

**Observability metric**: track `llm.rejection_rate` = number of rejected clusters / total LLM clusters. In alpha, rate > 5-10% = signal that something is wrong (either LLM regressed, or threshold too strict).

---

## Internationalization

V0 alpha: country fixed at `"FR"`, locale `"fr-FR"`:
- Source: `ratis_settings.json` § `default_country` or env var `RATIS_COUNTRY` (session override)
- The LLM uses these values to identify France-specific boilerplate

**Planned evolution**:
- **V1 multi-store**: country detected from `stores.country` (already present in the schema). Fallback `default_country` if store unknown.
- **V2 i18n**: `dismissal_category` enriched per country. The English prompt remains universal — only the `country` param changes.
- **V3** (post-launch): multi-language prompt if ever necessary (unlikely, English is sufficient to drive the LLM).

**Why the prompt is in English**: LLMs (Claude, GPT) are better trained in English. Switching the prompt to French would lose 5-10% instruction-following quality for zero benefit (receipts are already multi-language text depending on the country).

---

## Cache + feedback loop ocr_knowledge

Unchanged in structure (`ocr_knowledge` table schema preserved). Evolution in the flow:

**Pre-LLM lookup Stage 1b** (full-sequence, most discriminating): unchanged.

**Post-LLM feedback upsert Stage 2d**:
```sql
-- ONLY for clusters that PASSED the similarity guard
INSERT INTO ocr_knowledge
  (raw_ocr, corrected, match_type, source, confidence, type, dismissal_category)
VALUES
  (:raw_winner, :corrected_text, 'sequence', 'llm',
   :confidence_numeric,
   :type,                  -- 'product_name' | 'dismissal'
   :dismissal_category)    -- only when type='dismissal'
ON CONFLICT (raw_ocr, type) DO UPDATE SET
  seen_count = ocr_knowledge.seen_count + 1,
  corrected = EXCLUDED.corrected,
  confidence = EXCLUDED.confidence
```

**Confidence mapping v2**: no more "high/medium/low/none" from the LLM. Confidence is computed on the worker side from the similarity guard ratio:
- ratio >= 0.95 → 0.95 (denoise near-identity, very high confidence)
- ratio >= 0.85 → 0.85 (minor denoise, high confidence)
- ratio >= 0.7  → 0.7  (moderate denoise, medium)
- ratio >= 0.6  → 0.6  (at the accepted limit, low)
- ratio < 0.6   → REJECTED, no upsert (see guard)

**Important**: no upsert if the guard rejects (`ratio < 0.6`). The goal is to NOT pollute the cache with hallucinations. See the COUS COUS incident 2026-04-28 (ARCH v1) where conf=0.3 polluted the cache uselessly.

**Persist legacy parser output in parallel** (adjacent change): `scan_debug` field renamed `legacy_receipt_data` → `final_receipt_data` (= what is used for the scan). Additionally, we **actually** run `parse_receipt(ocr_winner)` in parallel and store its output in a new field `legacy_parser_output`. Enables true side-by-side comparison in the viewer.

---

## Acted decisions

### DA — local-first (LLM = safety net)

(Unchanged v1.) The pipeline first tries everything locally. The LLM only intervenes in Stage 2 on the residue.

### DA — LLM = denoise + classify only (NEW v2)

**Choice**: the LLM no longer proposes an EAN. Its role is strictly limited to (a) correcting character-level errors between OCR variants and (b) classifying the cluster type. Product matching remains 100% local fuzzy.

**Rationale**: alpha observation 2026-04-28, the LLM was hallucinating `scanned_name` values (e.g. COUS COUS from UCHA KOMBUCHA ANAN) because it was trying to "complete" DB candidates. Without candidates as input, no temptation. Local fuzzy matching does its job on clean denoised text.

**Rejected alternative**: LLM-as-matcher with candidates (v1). Too susceptible to hallucinations. Separation of concerns (LLM = OCR cleanup, fuzzy = product resolution) is more robust.

### DA — LLM input = clusters by bbox from 3 passes (NEW v2)

**Choice**: for each residue block, we send the 5 versions of the same block to the LLM (1 per pass) instead of sending just the winner.

**Rationale**: allows the LLM to reconcile variants among themselves, which is its true added value. With a single winner as input, the LLM just "rewrites" — with 3 variants, it can **compare** and **choose**. This is the scenario where the LLM is most useful.

**Cost**: ~5× more input tokens. Acceptable in alpha (philosophy "legible alpha > extreme token minimization" — per user 2026-04-28).

### DA — similarity guard 0.6 on worker side (NEW v2)

**Choice**: for each cluster, compare LLM `corrected_text` with raw OCR `raw_winner` via `SequenceMatcher.ratio()`. If ratio < 0.6 → reject LLM, keep raw OCR.

**Rationale**: safety net independent of the LLM. If the LLM strays too far (hallucination, or model bug), the worker detects and corrects on the code side. Threshold 0.6 calibrated empirically (see guard section).

**Cost**: marginal (1 SequenceMatcher call per cluster, ~ms).

### DA — country param injected into the prompt (NEW v2)

**Choice**: the LLM prompt is in English (universal, better instruction-following). The `country` param + `receipt_locale` contextualizes local boilerplate (BIENVENUE for FR, WELCOME for EN). V0 = "FR" fixed; V1 = lookup `stores.country`; V2 = dismissal_category dictionary per country.

**Rejected alternative**: full French prompt. Rejected because (a) lower LLM quality, (b) rewriting the prompt per locale = heavy maintenance, (c) not universal.

### DA — `corrected_name` in cashier format (preserved v1)

(Unchanged.) The LLM `corrected_text` reflects the text as printed on the receipt, denoised. Preserves cashier abbreviations (BRE, SAV, FRSE). Ground truth for future PaddleOCR fine-tuning.

### DA — minimum length gate for fuzzy (preserved v1)

(Unchanged.) `token_min_length=6` in the local matcher. The LLM denoise has no gate (it operates on full variants, not isolated tokens).

### DA — single LLM call (preserved v1, but scope revised)

**Choice**: a single LLM call per scan, processing the entire residue as a batch. But **scope revised**: before = LLM matches EAN + classifies. After = LLM denoises + classifies. No EAN.

### DA — graceful fallback when LLM fails (reinforced v2)

**Choice**: if LLM returns `null` on a cluster OR if the similarity guard rejects → keep `raw_winner` as `scanned_name`, `corrected_name = None`. Local fuzzy matching still attempts on raw_winner.

**Rationale**: maximum robustness. If the LLM is down, failed, or hallucinating, the pipeline does not break — it degrades gracefully to raw OCR.

### DA — `legacy_receipt_data` renamed `final_receipt_data` + true parallel parse (NEW v2)

**Choice**: (a) rename the scan_debug field so it reflects its true semantics (= receipt_data used to create the scan, not legacy parser output). (b) Add a new `legacy_parser_output` field that **actually** contains the `parse_receipt()` output running in parallel, for side-by-side comparison in the viewer.

**Rationale**: the current naming misled the user on 2026-04-28 (they believed the legacy parser was producing COUS COUS, when it was actually the LLM output). The viewer must display both separately. Minimal alembic migration (1 column rename + 1 column add).

---

## Pending decisions

### DP — GIN trgm index on `products.brands`

(Preserved v1, already applied via PR #163.)

### DP — PaddleOCR pipeline fine-tuning

(Preserved v1.) Out of scope V0, to reassess post-alpha with 5k+ pairs.

### DP — observability metrics

Additional tracking vs v1:
- `llm.rejection_rate` (% clusters rejected by similarity guard) — alert if > 10%
- `llm.unresolved_rate` (% clusters where LLM returns `null`) — signal of OCR difficulty
- `cluster.size_distribution` (average number of variants per cluster)

→ Logged via structured `logger.info`. Sentry dashboard post-V0.

### DP — y_tolerance cluster_by_bbox

V0 = ±10px. Could be made adaptive (depends on image resolution, character height). Out-of-scope V0, to refine if incorrect merges/splits are observed.

### DP — OFF data corrections (V2 out-of-scope)

(Unchanged v1.) OFF data can be inconsistent. V2 will explore an override layer + bot contribution.

---

## Implementation checklist

### Phase 1 — Local pre-filter (DEPLOYED)

(See PR #163 — local_prefilter.py + receipt_task refacto. No v2 changes.)

### Phase 2 (v2) — Cluster + LLM denoise + classify + Guard ✅ DEPLOYED (PR #166)

**Phase 2a — Cluster by bbox** ✅

- [x] `worker/pipeline/cluster_blocks.py`: `Cluster`, `ClusterVariant`, `cluster_blocks_by_bbox`
- [x] Seed-from-winner + attach-by-y±tolerance + orphan handling algorithm
- [x] Tests `tests/test_cluster_blocks.py` (11 tests)

**Phase 2b — LLM denoise + classify** ✅

- [x] `worker/pipeline/llm_filter.py` extended: `ClusterResult`, `LlmDenoiseOutput`, `denoise_clusters` API
- [x] Verbatim EN prompt copied into `_DENOISE_SYSTEM_PROMPT_TEMPLATE` (examples + anti-examples)
- [x] Country / locale params injected via `_denoise_system_prompt`
- [x] Backward-compat parser: `parse_llm_dispatch` routes v1 (`products[]`) vs v2 (`results[]`)
- [x] `worker/pipeline/llm_orchestrator.py`: `denoise_clusters_and_learn` (v2). v1 `filter_and_learn` temporarily preserved.
- [x] Tests `tests/test_llm_filter_v2.py` (18 tests)

**Phase 2c — Similarity guard** ✅

- [x] Separate helper `worker/pipeline/similarity_guard.py` (testability) — `validate_llm_corrected`, `compute_similarity`
- [x] Setting `ratis_settings.json § llm.similarity_guard_threshold = 0.6` (DA — not hardcoded)
- [x] Application: `_apply_similarity_guard_to_clusters` in `receipt_task.py` + structured logs (`llm.rejected_hallucination`, `llm.cluster_unresolved`, `llm.accepted_denoise`, `llm.guard_summary`)
- [x] Tests `tests/test_similarity_guard.py` (14 tests)

**Phase 2d — Worker integration v2** ✅

- [x] `_persist_llm_knowledge_v2`: upsert only accepted clusters (DA — guard prevents cache pollution)
- [x] `_ratio_to_numeric_confidence`: ratio → confidence mapping (0.6/0.7/0.85/0.95)
- [x] `_run_local_then_llm_v2`: Stage 1 + cluster + LLM denoise + guard + persist
- [x] `process_receipt`: v2 wired as side-effect (cache enrichment + observability) under `LLM_V2_ENABLED=true`. receipt_data still derived from v1 path for V0 — Phase 2g will remove v1 when multi-pass capture is ready.
- [x] Anti-hallucination + e2e tests (10 + 3 tests)

**Phase 2e — scan_debug rename + parallel legacy parser** ✅

- [x] Alembic migration `20260428_1300_scan_debug_v2_rename.py`: rename `legacy_receipt_data` → `final_receipt_data` + add `legacy_parser_output JSONB` (idempotent)
- [x] `ScanDebug` model aligned
- [x] Worker `_persist_scan_debug` accepts new kwargs + back-compat alias `legacy_receipt_data`
- [x] `parse_receipt(ocr_winner)` always running in parallel in `process_receipt`
- [x] Admin endpoint exposes both fields + back-compat alias
- [x] `scripts/scan_debug_viewer.py`: distinct sections

**Phase 2f — Drop dead code** ✅

- [x] **DA Phase 2f Option A enacted**
- [x] Deleted: `worker/pipeline/token_extractor.py`, `tests/test_token_extractor.py`, `tests/test_matcher_find_candidates.py`
- [x] Dropped: `matcher.find_candidates`, `flavor_tokens` from ratis_settings.json, `_compute_candidates_for_residue`, `candidates_by_block` argument

**Phase 2g — Multi-pass cluster capture** ✅

- [x] Extend `OcrPipelineResult` with `rich_blocks_by_pass: dict[str, list[RichOcrBlock]]` (back-compat alias `rich_blocks` still populated with corrected pass)
- [x] Refacto `_run_ocr_pipeline`: `recognize_rich` called per pass via helper `_capture_rich(name, img)` (corrected/clahe/binarized + inverted when fallback)
- [x] `_build_clusters_from_pipeline(residue, *, rich_blocks_by_pass=None)`: seed from residue (corrected post-Stage-1) then attach clahe/binarized/inverted variants; orphans (clusters without `corrected` variant) dropped to avoid re-querying Stage-1-resolved lines
- [x] `_run_local_then_llm_v2` accepts a `rich_blocks_by_pass` keyword (defaults to None for back-compat with non-migrated callers / existing tests)
- [x] Caller (`process_receipt`) passes `pipeline_result.rich_blocks_by_pass` → LLM finally sees real 3-variant clusters

**Phase 2h — Retire v1 path + hybrid LLM denoise + regex prices** ✅

Architectural decision (user 2026-04-28): the LLM v2 must NOT extract prices. Its value = denoise + classify. Prices on receipts have a strict and deterministic format → `parser._PRICE_RE` extracts them without hallucination risk.

- [x] `worker/pipeline/price_extractor.py`: `extract_prices_from_rich_blocks` + `find_price_for_cluster` (regex on rich blocks, spatial association by y±tolerance)
- [x] `_v2_output_to_receipt_data`: assembles `ReceiptData` from cluster decisions + regex prices (product cluster → ScannedItem.scanned_name=raw, .corrected_name=LLM-denoised when guard passed, .price=regex match at nearest y)
- [x] `process_receipt`: v2 = unique LLM path (drop v1 path entirely). Legacy `parse_receipt` keeps only the role of last-resort fallback when LLM is unavailable OR produces zero usable items.
- [x] Drop v1 entirely: `_run_local_then_llm`, `_try_llm_filter`, `filter_and_learn`, `_DENOISE_ADDENDUM`, `LlmReceiptOutput`, `Product`/`Retailer`/`Dismissal` (LLM-side), `parse_llm_dispatch`, `_llm_output_to_receipt_data`, `_llm_filter_enabled`, `_llm_v2_enabled`, `bulk_upsert_dismissals` (the v1 writer; the reader `get_known_dismissals` remains for Stage 1).
- [x] Drop env vars `LLM_FILTER_ENABLED` (v1 gate) and `LLM_V2_ENABLED` (transition flag): provisioning `LLM_API_KEY` is the only switch.
- [x] `ScannedItem.corrected_name` added (forward path for Phase 4 scan-history UI).

### Phase 3 — UI scan-history denoised (P1 post-V0)

(Unchanged v1.) UI displays `corrected_name` when non-null, otherwise `scanned_name` (raw).

### Phase 4 — User correction loop (P2 post-V0)

(Unchanged v1.)

### Phase 5 — PaddleOCR fine-tuning prep (post-alpha)

(Unchanged v1, with vision-LLM verification loop to correct token fusions.)

---

## Tests

### Unit

- `test_cluster_blocks.py`:
  - normal clustering (3 variants per bbox)
  - cluster with a single variant (isolated block)
  - cluster that splits (2 adjacent lines y±tolerance)
  - configurable tolerance
- `test_llm_filter_v2.py`:
  - parsing new schema `LlmDenoiseOutput`
  - backward-compat parsing old schema `LlmReceiptOutput.products[]`
  - country param injected into the prompt
- `test_similarity_guard.py`:
  - identical ratio → accepted
  - boundary ratio (exactly 0.6) → accepted
  - ratio below threshold (0.5) → rejected
  - corrected None → rejected
  - empty raw → rejected
- `test_ocr_knowledge_persist_v2.py`:
  - accepted clusters → upserted with correct confidence
  - rejected clusters → NO upsert (cache pollution prevented)
  - confidence mapping from ratio

### Integration

- `test_receipt_pipeline_v2_local_only.py`:
  - Fixture: receipt with 5 product blocks all present in `ocr_knowledge` conf>=0.8
  - Verify: Stage 1 resolves all, empty residue, LLM mock NOT called
- `test_receipt_pipeline_v2_residue_only.py`:
  - Fixture: receipt with 3 blocks, 1 cache hit, 2 unknown
  - Verify: 1 resolved locally, residue = 2 blocks, LLM called on clusters of these 2
  - Verify: LLM output passes the guard → 2 new entries upserted in `ocr_knowledge` source='llm'
- `test_receipt_pipeline_v2_anti_hallucination.py`:
  - Fixture: raw OCR "UCHA KOMBUCHA ANAN", LLM mock returns `corrected_text="COUS COUS"`
  - Verify: guard rejects (ratio 0.10), `scanned_name="UCHA KOMBUCHA ANAN"`, no ocr_knowledge upsert
  - Verify: `llm.rejected_hallucination` log emitted
- `test_receipt_pipeline_v2_unresolved.py`:
  - Fixture: LLM returns `corrected_text=None` for a cluster
  - Verify: `scanned_name = raw`, `corrected_name=None`, `llm.cluster_unresolved` log
- `test_receipt_pipeline_v2_e2e.py`:
  - Fixture: real Monoprix receipt (UCHA KOMBUCHA ANAN + 2 X-MOUSSE-CHOCO-LAI + GIRASOLI FROM-CHEV)
  - Verify: 3 clusters, LLM corrects fusions/typos, guard accepts all, EANs matched via local fuzzy

### Production validation

Post-v2 deploy, re-scan the Monoprix receipt that had hallucinated COUS COUS → verify that this time `scanned_name` remains faithful to the OCR. Track `llm.rejection_rate` over ~50 alpha scans to validate the guard does not reject too many legitimate cases.

---

## Glossary

- **Local-first**: architecture where the pipeline resolves as much as possible locally (cache + DB) before any LLM call. (v1)
- **LLM denoise-only**: v2 — the LLM ONLY corrects character-level OCR errors + classifies the cluster type. No EAN matching.
- **Cluster (by bbox)**: group of OCR variants of the same physical block on the receipt, extracted by N preprocessing passes at the same y (±tolerance).
- **Variant**: one OCR reading of the same cluster by a given pass (corrected, clahe, binarized).
- **Similarity guard**: worker-side post-LLM test. Compares `corrected_text` to `raw_winner` via `SequenceMatcher.ratio()`. If < threshold (0.6) → REJECT, keep raw OCR.
- **Hallucination**: LLM output that deviates semantically from the raw OCR beyond what realistic OCR errors can explain (e.g.: COUS COUS from UCHA KOMBUCHA ANAN).
- **Residue**: set of Stage 1 unclassified blocks. Only these blocks are passed to the LLM in Stage 2.
- **Country param**: receipt country, passed to the LLM to contextualize local boilerplate. V0 fixed FR, V1 lookup stores.country, V2 full i18n.
- **OCR raw**: raw text output from PaddleOCR, with possible character confusions.
- **Cashier-format text**: text as printed on the receipt (UPPERCASE, abbreviations, no accents).
- **Corrected text**: raw OCR denoised by the LLM then validated by the guard. Ground truth for PaddleOCR fine-tuning.
- **Canonical name (OFF)**: marketing name in Open Food Facts. Different from cashier-format.
- **Matched EAN**: OFF reference resolved by local fuzzy on `corrected_text` (or raw if guard rejected). **No longer output by the LLM in v2.**
- **Cache hit**: `ocr_knowledge.lookup(raw_ocr, type)` returns a row with confidence ≥ 0.8 → block classified in Stage 1.
- **Feedback loop**: post-LLM upsert in `ocr_knowledge` Stage 2d (only clusters accepted by guard) that enriches the cache.
- **Self-reinforcing**: each scan makes subsequent ones faster + cheaper (cache convergence).
- **`final_receipt_data`** (renamed): `scan_debug` field that contains the ReceiptData used to create the scan (= converted LLM output, or legacy fallback).
- **`legacy_parser_output`** (NEW): `scan_debug` field that contains the `parse_receipt()` output running **in parallel** for side-by-side comparison in the viewer.

---

## References

- [[ARCH_PRODUCT_ANALYSER]] — main OCR + LLM pipeline
- [[TRAINING]] — auto-learning data flow
- KP-31 — Anthropic prompt cache 4096 tokens minimum (cf KNOWN_PROBLEMS.md)
- AF-16 — LLM confuses prices vs totals (docs/audits/ALPHA_FEEDBACK.md)
- AF-18, AF-19, AF-20 — store_detector + UI flow gaps (sessions 2026-04-27/28)
- PR #122 — dismissal feedback loop (exists, base for Stage 1a)
- PR #163 — Phase 1+2 v1 deployment (local-first base)
- Incident 2026-04-28 — scan `a61d2f67-c3fd-4e2e-aa96-309a352ee2c0` LLM hallucination COUS COUS from UCHA KOMBUCHA ANAN, motive for v2 redesign
