// ratis_client/hooks/use-list-items.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { listClient } from '@/services/list-client';
import type { ShoppingListItem } from '@/types/shopping-list';

export interface AddItemVars {
  product_ean: string;
  quantity?: number;
}

export interface PatchItemVars {
  itemId: string;
  checked?: boolean;
  quantity?: number;
}

export interface DeleteItemVars {
  itemId: string;
}

/**
 * POST /lists/{listId}/items — add a product to the list.
 * Backend requires `product_ean` (non-null string).
 * Invalidates ['list', listId], ['route', listId] (the route becomes
 * stale), and ['default-suggestions'] (the freshly added EAN should
 * appear in tier (c) the next time the AddBar dropdown opens empty —
 * see ``docs/superpowers/specs/2026-05-14-default-search-3tier-design.md``).
 */
export function useAddItem(listId: string | null) {
  const qc = useQueryClient();
  return useMutation<ShoppingListItem, Error, AddItemVars>({
    mutationFn: async ({ product_ean, quantity }) => {
      if (!listId) throw new Error('no_active_list');
      return listClient.post<ShoppingListItem>(`/lists/${listId}/items`, {
        product_ean,
        quantity: quantity ?? 1,
      });
    },
    onSuccess: () => {
      if (!listId) return;
      qc.invalidateQueries({ queryKey: ['list', listId] });
      qc.invalidateQueries({ queryKey: ['route', listId] });
      // Wave-13 — the new EAN becomes part of the user's history, so
      // the next default-suggestions dropdown should reflect it
      // without waiting for the 5 min staleTime.
      qc.invalidateQueries({ queryKey: ['default-suggestions'] });
    },
  });
}

/**
 * PATCH /lists/{listId}/items/{itemId} — update checked or quantity.
 * Only invalidates ['list', listId] (toggling checked doesn't change the route).
 */
export function useToggleItem(listId: string | null) {
  const qc = useQueryClient();
  return useMutation<ShoppingListItem, Error, PatchItemVars>({
    mutationFn: async ({ itemId, checked, quantity }) => {
      if (!listId) throw new Error('no_active_list');
      const body: Record<string, unknown> = {};
      if (checked !== undefined) body.checked = checked;
      if (quantity !== undefined) body.quantity = quantity;
      return listClient.patch<ShoppingListItem>(
        `/lists/${listId}/items/${itemId}`,
        body,
      );
    },
    onSuccess: () => {
      if (listId) qc.invalidateQueries({ queryKey: ['list', listId] });
    },
  });
}

/**
 * DELETE /lists/{listId}/items/{itemId}.
 * Invalidates ['list', listId] and ['route', listId] (the route becomes stale).
 */
export function useDeleteItem(listId: string | null) {
  const qc = useQueryClient();
  return useMutation<void, Error, DeleteItemVars>({
    mutationFn: async ({ itemId }) => {
      if (!listId) throw new Error('no_active_list');
      await listClient.delete<void>(`/lists/${listId}/items/${itemId}`);
    },
    onSuccess: () => {
      if (!listId) return;
      qc.invalidateQueries({ queryKey: ['list', listId] });
      qc.invalidateQueries({ queryKey: ['route', listId] });
    },
  });
}
