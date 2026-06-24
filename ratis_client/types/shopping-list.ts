// ratis_client/types/shopping-list.ts

/**
 * Canonical Ratis category keys for shopping-list rows. Derived by
 * ``ratis_list_optimiser.services.category_mapper.resolve_category``
 * server-side (wave 12 — PO ticket 2026-05-14). The FE consumes the
 * snake-case key + owns the French display label via i18n.
 *
 * Display order is defined in ``constants/list-categories.ts``.
 */
export type ListCategoryKey =
  | 'frais'
  | 'boulangerie'
  | 'epicerie'
  | 'boissons'
  | 'vrac'
  | 'autres';

/**
 * Item of a shopping list (returned by GET /lists/{id}).
 */
export interface ShoppingListItem {
  id: string;
  product_ean: string;
  product_name: string;
  quantity: number;
  checked: boolean;
  checked_at: string | null;
  /**
   * Server-derived category key (wave 12). ``null`` when the item has
   * no resolved product row (defensive — should not happen for items
   * created through ``POST /lists/{id}/items`` which validates the EAN).
   */
  category: ListCategoryKey | null;
}

/**
 * Summary of a shopping list, as returned by GET /lists.
 * Does NOT include items — fetch /lists/{id} for details.
 */
export interface ShoppingListSummary {
  id: string;
  name: string | null;
  has_default_name: boolean;
  is_template: boolean;
  item_count: number;
  unchecked_count: number;
  created_at: string;
  updated_at: string;
}

/**
 * Full shopping list with items, as returned by GET /lists/{id}.
 */
export interface ShoppingList {
  id: string;
  name: string | null;
  has_default_name: boolean;
  is_template: boolean;
  items: ShoppingListItem[];
  created_at: string;
  updated_at: string;
}
