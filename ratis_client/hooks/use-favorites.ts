// ratis_client/hooks/use-favorites.ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';

export interface FavoriteProduct {
  ean: string;
  name: string;
  photo_url_small: string | null;
  created_at: string | null;
}

export interface FavoritesResponse {
  items: FavoriteProduct[];
}

/**
 * List the user's favorite products. GET /product/favorites.
 */
export function useFavorites() {
  return useQuery<FavoritesResponse>({
    queryKey: ['favorites'],
    queryFn: () => productClient.get<FavoritesResponse>('/product/favorites'),
  });
}

export interface ToggleFavoriteInput {
  ean: string;
  favorited: boolean;
}

/**
 * Toggle a product's favorite status.
 * - favorited=true  → POST /product/{ean}/favorite (add, idempotent)
 * - favorited=false → DELETE /product/{ean}/favorite (remove, idempotent)
 */
export function useToggleFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ean, favorited }: ToggleFavoriteInput) =>
      favorited
        ? productClient.post(`/product/${ean}/favorite`)
        : productClient.delete(`/product/${ean}/favorite`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['favorites'] }),
  });
}

/**
 * Derived: is the given ean in the favorites list?
 * Uses the same query cache as useFavorites (no extra fetch).
 */
export function useIsFavorite(ean: string | undefined): boolean {
  const { data } = useFavorites();
  if (!ean) return false;
  return data?.items.some(f => f.ean === ean) ?? false;
}
