// ratis_client/hooks/use-enrichissement.ts
import { useQuery } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';
import { EnrichissementTask } from '@/types/gamification';

interface IncompleteProductsResponse {
  items: EnrichissementTask[];
}

export function useEnrichissement() {
  return useQuery<EnrichissementTask | null>({
    queryKey: ['enrichissement'],
    queryFn: async () => {
      // The enrichissement card is silently hidden when no task is available.
      // We swallow all errors and return null so the hook never enters isError state,
      // keeping the dashboard resilient to product_analyser downtime.
      //
      // Endpoint contract — matches `GET /api/v1/product/incomplete?limit=N`
      // (singular `product`, batched `{ items: [...] }` shape — shipped via
      // PR #453). Pre-fix this hook hit a non-existent `/products/incomplete`
      // (plural) and expected a bare EnrichissementTask : the 404 was silently
      // swallowed by the catch, the dashboard card stayed hidden in prod even
      // when the backend had tasks to surface.
      try {
        const response = await productClient.get<IncompleteProductsResponse>(
          '/product/incomplete?limit=1',
        );
        return response.items[0] ?? null;
      } catch {
        return null;
      }
    },
  });
}
