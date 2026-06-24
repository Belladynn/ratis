// ratis_client/types/shop.ts
//
// Boutique V1 — shared types for the shop catalog + order endpoints.
//
// These shapes mirror the Pydantic schemas defined in
// `webservices/ratis_rewards/schemas/boutique.py` (cf
// `webservices/ratis_rewards/ARCH_boutique.md` § Endpoints).

export interface ShopBrand {
  id: string;
  name: string;
  logo_url: string | null;
  is_active: boolean;
}

export interface ShopCatalogResponse {
  brands: ShopBrand[];
}

export interface ShopOrderInput {
  brand_id: string;
  /** Always in cents — V1 ∈ {500, 1000, 2000, 5000}. */
  denomination_cents: number;
}

export interface ShopOrderResponse {
  order_id: string;
  brand: string;
  denomination_cents: number;
  cab_cost: number;
  new_cab_balance: number;
  status: 'pending' | 'issued' | 'failed';
  estimated_arrival: string;
}

/** Cents → CAB conversion (V1 fixed ratio — synced with backend ARCH). */
export const CAB_PER_EUR = 5_000;

/** V1 denominations in cents (5/10/20/50€). */
export const V1_DENOMINATIONS_CENTS = [500, 1000, 2000, 5000] as const;

export function cabCostFor(denominationCents: number): number {
  return Math.round((denominationCents / 100) * CAB_PER_EUR);
}
