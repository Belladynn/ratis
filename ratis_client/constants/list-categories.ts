// ratis_client/constants/list-categories.ts
//
// Wave 12 (PO ticket 2026-05-14) — canonical FE-side metadata for the
// Liste tab category grouping. The backend resolves the snake-case key
// (frais / boulangerie / epicerie / boissons / vrac / autres) ; this
// file owns the display order + French label so the UI never has to
// hardcode either.

import type { ListCategoryKey } from '@/types/shopping-list';

/**
 * Section render order — locked by PO directive 2026-05-14. The Liste
 * tab iterates this array to render section headers ; ``autres`` always
 * lands last so the « catch-all » bucket doesn't visually outrank the
 * curated categories above it.
 */
export const LIST_CATEGORY_ORDER: readonly ListCategoryKey[] = [
  'frais',
  'boulangerie',
  'epicerie',
  'boissons',
  'vrac',
  'autres',
] as const;

/**
 * Maps a category key to its i18n leaf key under ``liste.category.<key>``
 * — the FE component reads ``t('liste.category.' + key)`` directly, so
 * no helper lookup is needed. Kept as documentation for the consumer.
 *
 * French labels :
 *   frais        → « Frais alimentaire »
 *   boulangerie  → « Boulangerie »
 *   epicerie     → « Épicerie »
 *   boissons     → « Boissons »
 *   vrac         → « Vrac »
 *   autres       → « Autres »
 */
export const LIST_CATEGORY_I18N_PREFIX = 'liste.category' as const;

/**
 * Fallback key for any item whose ``category`` field came back ``null``
 * from the backend (very rare — see ``ShoppingListItem.category``).
 */
export const LIST_CATEGORY_FALLBACK: ListCategoryKey = 'autres';
