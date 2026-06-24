// ratis_client/hooks/use-product-search.ts
//
// Product text search (wave 4 Bug 3) — debounced React Query hook
// powering the Liste tab AddBar autocomplete and the Produit tab
// search input.
//
// Contract :
//   - Backend : GET /api/v1/product/search?q={query}&limit={limit}
//   - Empty/whitespace query → hook is disabled (no fetch). Use
//     ``useDefaultSuggestions`` for empty-state suggestions
//     (wave-13 PO ticket 2026-05-14, dedicated tier-composed endpoint).
//   - Typed queries go through the 300 ms debounce.
//   - The hook is always called (rules of hooks) but the underlying
//     useQuery is disabled until the query is valid OR the caller sets
//     enabled=false.

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { productClient } from '@/services/product-client';

export interface ProductSearchHit {
  ean: string;
  name: string;
  brands: string | null;
  /**
   * Display string for the packaging quantity (« 1 kg », « 500 g »,
   * « 6 x 33 cl », …). Sourced from ``products.quantity_text`` on the
   * backend. Surfaced wave 9 (PO « pomme de terre » duplicate disambig)
   * so the dropdown row can show a secondary line letting the user
   * distinguish identical-named products.
   */
  quantity: string | null;
  categories_tags: string[] | null;
  /**
   * OFF ``labels_tags`` — used by the FE row to render the 🌱 BIO
   * badge when any organic signal is present (cf
   * ``isOrganicHit`` helper in ``components/liste/add-bar.tsx``).
   */
  labels_tags: string[] | null;
  /**
   * OFF ``origins_tags`` — used by the FE row to render the 🇫🇷 flag
   * when a French-origin signal is present (matches the Phase C-2
   * ``is_french_product`` whitelist).
   */
  origins_tags: string[] | null;
  source: string;
}

export interface ProductSearchResponse {
  items: ProductSearchHit[];
}

export interface UseProductSearchOptions {
  /** Override the default debounce window (ms). */
  debounceMs?: number;
  /** Hard upper bound — matches the backend cap (50). */
  limit?: number;
  /** Caller-side disable (e.g. the search panel is hidden). */
  enabled?: boolean;
}

const DEFAULT_DEBOUNCE_MS = 300;
const DEFAULT_LIMIT = 20;

export function useProductSearch(
  query: string,
  opts: UseProductSearchOptions = {},
) {
  const {
    debounceMs = DEFAULT_DEBOUNCE_MS,
    limit = DEFAULT_LIMIT,
    enabled = true,
  } = opts;

  const trimmed = query.trim();
  const [debouncedQuery, setDebouncedQuery] = useState('');

  useEffect(() => {
    if (trimmed.length === 0) {
      // Empty input. Clear the debounced value so a previous typed
      // result does not linger.
      setDebouncedQuery('');
      return;
    }
    const t = setTimeout(() => setDebouncedQuery(trimmed), debounceMs);
    return () => clearTimeout(t);
  }, [trimmed, debounceMs]);

  // A typed query (length ≥ 1) is queryable once the debounce fires.
  // Empty/whitespace input → hook is disabled (see useDefaultSuggestions
  // for empty-state suggestions).
  const isQueryable = enabled && debouncedQuery.length >= 1;

  return useQuery<ProductSearchResponse>({
    queryKey: ['product-search', debouncedQuery, limit],
    queryFn: () => {
      // ``encodeURIComponent`` is used directly (instead of
      // ``URLSearchParams``) because the FastAPI ``Query(...)`` parser
      // doesn't decode ``+`` as a space — it expects ``%20``. RFC 3986
      // compliance > form-encoded shortcut here.
      const q = encodeURIComponent(debouncedQuery);
      return productClient.get<ProductSearchResponse>(
        `/product/search?q=${q}&limit=${limit}`,
      );
    },
    enabled: isQueryable,
    staleTime: 30_000,
  });
}
