// ratis_client/hooks/use-default-suggestions.ts
//
// Default search suggestions hook — consumes GET /product/suggestions/default
// for the Liste tab AddBar and Produit tab search field empty-state.
//
// Contract :
//   - Backend : GET /api/v1/product/suggestions/default?limit={limit}
//   - Returns a tier-composed list (user history topped up with curated
//     French staples) — see
//     docs/superpowers/specs/2026-05-14-default-search-3tier-design.md
//   - Always-on (no `enabled` flag required for prefetch use cases —
//     screens call this at mount to warm the cache).
//
// staleTime is 5 min : suggestions don't change often, and the
// `useAddItem` mutation invalidates this queryKey on success so freshly
// added items appear in the next dropdown opening (no stale wait).

import { useQuery } from '@tanstack/react-query';

import { productClient } from '@/services/product-client';
import type { ProductSearchHit } from '@/hooks/use-product-search';

export interface DefaultSuggestionsResponse {
  items: ProductSearchHit[];
}

export interface UseDefaultSuggestionsOptions {
  limit?: number;
}

const DEFAULT_LIMIT = 5;

export function useDefaultSuggestions(
  opts: UseDefaultSuggestionsOptions = {},
) {
  const { limit = DEFAULT_LIMIT } = opts;
  return useQuery<DefaultSuggestionsResponse>({
    queryKey: ['default-suggestions', limit],
    queryFn: () =>
      productClient.get<DefaultSuggestionsResponse>(
        `/product/suggestions/default?limit=${limit}`,
      ),
    staleTime: 5 * 60 * 1000, // 5 min
  });
}
