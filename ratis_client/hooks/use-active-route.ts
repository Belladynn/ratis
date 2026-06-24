// ratis_client/hooks/use-active-route.ts
import { useQuery } from '@tanstack/react-query';
import { listClient } from '@/services/list-client';
import { AuthError } from '@/types/auth';

export type RouteStatus = 'computing' | 'ready' | 'failed';

export type PriceSource = 'consensus' | 'national_average' | 'unknown';

export interface RouteItem {
  item_id: string;
  product_ean: string;
  product_name: string;
  quantity: number;
  price: number | null;
  price_source: PriceSource;
  trust_score: number | null;
}

export interface RouteStore {
  store_id: string;
  store_name: string;
  retailer: string | null;
  address: string;
  lat: number;
  lng: number;
  order: number;
  subtotal: number;
  items: RouteItem[];
}

export interface RouteFull {
  id: string;
  list_id: string;
  status: 'ready';
  total_price: number;
  total_savings: number;
  distance_km: number | null;
  computed_at: string;
  expires_at: string;
  stores: RouteStore[];
  route_polyline: string | null;
  warnings: unknown[];
}

export interface RouteSlim {
  id: string;
  list_id: string;
  status: 'computing' | 'failed';
}

export type RouteResponse = RouteFull | RouteSlim;

/**
 * Fetch the latest optimized route for a shopping list.
 * Treats 404 (no_active_route / list_not_found) as `null` — this is the normal
 * state before the user has ever triggered an optimization.
 * Polls every 2 s while `status === 'computing'`.
 */
export function useActiveRoute(listId: string | null) {
  return useQuery<RouteResponse | null>({
    queryKey: ['route', listId],
    queryFn: async () => {
      try {
        return await listClient.get<RouteResponse>(`/lists/${listId}/route`);
      } catch (err) {
        if (err instanceof AuthError && err.httpStatus === 404) return null;
        throw err;
      }
    },
    enabled: !!listId,
    refetchInterval: (q) => {
      const data = q.state.data;
      if (data && 'status' in data && data.status === 'computing') return 2000;
      return false;
    },
  });
}
