// ratis_client/hooks/use-shopping-lists.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listClient } from '@/services/list-client';
import type { ShoppingListSummary } from '@/types/shopping-list';

/**
 * Fetch all shopping lists owned by the authenticated user.
 * Returns summaries (no items). To get items, call GET /lists/{id}.
 */
export function useShoppingLists() {
  return useQuery<ShoppingListSummary[]>({
    queryKey: ['lists'],
    queryFn: () => listClient.get<ShoppingListSummary[]>('/lists'),
  });
}

/**
 * Convenience wrapper — returns the first (active) list.
 * V1: one user = one list.
 */
export function useActiveList() {
  const { data, ...rest } = useShoppingLists();
  return { ...rest, data: data?.[0] ?? null };
}

/**
 * Create a shopping list. Wraps ``POST /lists`` (Pydantic
 * ``CreateListRequest { name?: str }``). Used by Liste tab when the
 * user attempts to add an item before having any list — we auto-create
 * a default list with name "Ma liste" then enchain the AddItem call.
 */
export function useCreateList() {
  const qc = useQueryClient();
  return useMutation<ShoppingListSummary, Error, { name?: string }>({
    mutationFn: (vars) =>
      listClient.post<ShoppingListSummary>('/lists', vars),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lists'] });
    },
  });
}
