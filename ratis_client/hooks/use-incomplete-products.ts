// ratis_client/hooks/use-incomplete-products.ts
//
// Batch fetch of incomplete-product tasks for the « Compléter ce
// produit » screen. The completer screen fetches once at mount
// (staleTime 0 — always fresh on open) and iterates locally in
// memory through the batch. Mutations (`useContributeField` after
// each submit) invalidate this queryKey so the next batch reflects
// the user's contributions.

import { useQuery } from '@tanstack/react-query';

import { productClient } from '@/services/product-client';
import type { EnrichissementTask } from '@/types/gamification';

export interface IncompleteProductsResponse {
  items: EnrichissementTask[];
}

export interface UseIncompleteProductsOptions {
  limit?: number;
}

const DEFAULT_LIMIT = 10;

export function useIncompleteProducts(
  opts: UseIncompleteProductsOptions = {},
) {
  const { limit = DEFAULT_LIMIT } = opts;
  return useQuery<IncompleteProductsResponse>({
    queryKey: ['incomplete-products', limit],
    queryFn: () =>
      productClient.get<IncompleteProductsResponse>(
        `/product/incomplete?limit=${limit}`,
      ),
    staleTime: 0, // always re-fetch on screen mount
  });
}
