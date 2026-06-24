// ratis_client/utils/product-search-hit.ts
//
// Pure helpers for rendering a ``ProductSearchHit`` row in the
// autocomplete dropdowns (Liste AddBar + Produit tab search).
//
// Wave 9 (PO ticket 2026-05-13) — « j'ai une liste massive de plein de
// pomme de terre et aucun moyen de les identifier précisement ». The
// dropdown row now shows a secondary line composing the most
// discriminating signals : brand · quantity · 🇫🇷 (french origin) ·
// 🌱 (organic). When all four signals are absent we surface a source
// hint (« OFF » / « Carte cadeau » via existing translations) so the
// user still knows where the row came from.
//
// Keep this module side-effect-free : no React imports, no i18next
// imports. Components own the JSX + the i18n lookups ; this module
// hands them ready-to-render strings.

import type { ProductSearchHit } from '@/hooks/use-product-search';

// Mirrors ``services.product_attributes._ORGANIC_SIGNALS`` server-side.
// Kept lowercase + as a Set for O(1) match. Adding a new signal here
// MUST also be reflected in the backend helper (single point of truth
// for the BIO badge ; cf ARCH_missions.md § Phase C-1).
const ORGANIC_SIGNALS = new Set<string>([
  'en:organic',
  'fr:bio',
  'en:eu-organic',
  'fr:agriculture-biologique',
]);

// Mirrors ``services.product_attributes._FRENCH_SIGNALS`` server-side.
// Same single-point-of-truth caveat ; if you add a French-origin
// variant here, mirror it server-side so the ORDER BY ``rank_french``
// bucket stays consistent with what the FE renders.
const FRENCH_SIGNALS = new Set<string>([
  'en:france',
  'fr:france',
  'en:made-in-france',
]);

/**
 * Returns ``true`` iff the hit carries an organic certification signal
 * in its ``labels_tags`` array (case-insensitive exact match).
 *
 * Mirrors ``services.product_attributes.is_organic_product`` on the
 * backend. Used by the dropdown to render the 🌱 badge.
 */
export function isOrganicHit(hit: Pick<ProductSearchHit, 'labels_tags'>): boolean {
  const tags = hit.labels_tags;
  if (!tags || tags.length === 0) return false;
  return tags.some((t) => ORGANIC_SIGNALS.has(t.toLowerCase()));
}

/**
 * Returns ``true`` iff the hit's ``origins_tags`` array contains a
 * French-origin signal (case-insensitive exact match). Mirrors
 * ``services.product_attributes.is_french_product``.
 */
export function isFrenchHit(hit: Pick<ProductSearchHit, 'origins_tags'>): boolean {
  const tags = hit.origins_tags;
  if (!tags || tags.length === 0) return false;
  return tags.some((t) => FRENCH_SIGNALS.has(t.toLowerCase()));
}

/**
 * Compose the secondary line of a search-hit dropdown row.
 *
 * Order (each segment separated by ` · `) :
 *   1. ``brands`` if present
 *   2. ``quantity`` if present
 *   3. 🇫🇷 if the hit is French (per ``isFrenchHit``)
 *   4. 🌱 if the hit is organic (per ``isOrganicHit``)
 *
 * Returns ``null`` when NONE of those signals is present so the
 * caller can render only the primary name line (no empty row eaten).
 * The caller decides whether to substitute a source hint fallback.
 *
 * The returned string is i18n-agnostic — brand and quantity are
 * already user-readable verbatim, and the flag / leaf emojis carry
 * universal meaning. No date / number formatting needed.
 */
export function composeSearchHitSecondary(
  hit: Pick<
    ProductSearchHit,
    'brands' | 'quantity' | 'origins_tags' | 'labels_tags'
  >,
): string | null {
  const parts: string[] = [];
  if (hit.brands && hit.brands.trim()) parts.push(hit.brands.trim());
  if (hit.quantity && hit.quantity.trim()) parts.push(hit.quantity.trim());
  if (isFrenchHit(hit)) parts.push('🇫🇷');
  if (isOrganicHit(hit)) parts.push('🌱');
  if (parts.length === 0) return null;
  return parts.join(' · ');
}
