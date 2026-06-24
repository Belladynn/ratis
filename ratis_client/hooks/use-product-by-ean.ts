// ratis_client/hooks/use-product-by-ean.ts
import { useQuery } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';

export interface ProductInfo {
  ean: string;
  name: string;
  brand: string | null;
  photo_url: string | null;
  storage_type: string | null;
  product_quantity: number | null;
  product_quantity_unit: string | null;
}

export interface LocalPrice {
  store_id: string;
  /** Integer number of cents — int-cents, never euros. */
  price_cents: number;
  last_seen_at: string;
}

export interface NearbyPrice {
  store_id: string;
  store_name: string;
  /** Integer number of cents — int-cents, never euros. */
  price_cents: number;
  distance_km: number;
}

export interface ProductDetailResponse {
  product: ProductInfo;
  local_price: LocalPrice | null;
  nearby_prices: NearbyPrice[];
}

export interface UseProductByEanOptions {
  lat?: number | null;
  lng?: number | null;
  storeId?: string | null;
}

export function useProductByEan(
  ean: string | null,
  opts: UseProductByEanOptions = {},
) {
  const { lat, lng, storeId } = opts;
  return useQuery<ProductDetailResponse>({
    queryKey: ['product', ean, lat ?? null, lng ?? null, storeId ?? null],
    queryFn: () => {
      const params = new URLSearchParams();
      if (lat != null) params.set('user_lat', String(lat));
      if (lng != null) params.set('user_lng', String(lng));
      if (storeId) params.set('store_id', storeId);
      const qs = params.toString();
      return productClient.get<ProductDetailResponse>(
        `/product/${ean}${qs ? '?' + qs : ''}`,
      );
    },
    enabled: !!ean,
    staleTime: 60_000, // 1min
  });
}
