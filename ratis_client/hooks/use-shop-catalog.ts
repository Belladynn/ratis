// ratis_client/hooks/use-shop-catalog.ts
//
// Boutique V1 — fetch the active gift-card brand catalog (current season).
//
// The backend (`GET /api/v1/rewards/gift-cards/catalog`) only returns brands
// where `is_active=true` (saisonnière rotation). 5 min stale time matches
// the rotation cadence — no need to thrash on every screen mount.

import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type { ShopCatalogResponse } from '@/types/shop';

const FIVE_MINUTES = 5 * 60 * 1000;

export function useShopCatalog() {
  return useQuery<ShopCatalogResponse>({
    queryKey: ['shop-catalog'],
    queryFn: () =>
      rewardsClient.get<ShopCatalogResponse>('/rewards/gift-cards/catalog'),
    staleTime: FIVE_MINUTES,
  });
}

export type { ShopBrand, ShopCatalogResponse } from '@/types/shop';
