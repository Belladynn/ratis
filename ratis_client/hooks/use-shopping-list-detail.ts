// ratis_client/hooks/use-shopping-list-detail.ts
import { useQuery } from '@tanstack/react-query';
import { listClient } from '@/services/list-client';
import type { ShoppingList } from '@/types/shopping-list';

/**
 * Alias — the full list detail shape returned by GET /lists/{id}.
 * Same shape as `ShoppingList` in types/shopping-list.ts.
 */
export type ShoppingListDetail = ShoppingList;

/**
 * Fetch a shopping list with its items.
 * Disabled when listId is null.
 */
export function useShoppingListDetail(listId: string | null) {
  return useQuery<ShoppingListDetail>({
    queryKey: ['list', listId],
    queryFn: () => listClient.get<ShoppingListDetail>(`/lists/${listId}`),
    enabled: !!listId,
  });
}
