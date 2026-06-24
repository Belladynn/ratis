// ratis_client/utils/roi-rings.ts

/**
 * Default subscription price (cents). The real value comes from the backend
 * via /account/stats → rings.subscription_price_cents ; this constant is only
 * a fallback used until the query resolves.
 */
export const DEFAULT_SUBSCRIPTION_PRICE_CENTS = 799; // 7.99€

const MAX_DISPLAY_FOSSILS = 10;

export interface RingState {
  totalAbonnements: number; // ex: 2.34
  completedRings: number; // ex: 2
  currentFill: number; // 0..1, ex: 0.34
  prestigeLevel: number; // 0 = pas de prestige, 1 = ★I, etc.
  displayFossils: number; // min(completedRings, 10)
}

/**
 * Computes the visual ring state from the raw total savings + subscription
 * price. Both inputs are in cents (integer). The subscription price is
 * parameterised so the backend can change the price without requiring a new
 * frontend build.
 */
export function computeRings(
  totalSavingsCents: number,
  subscriptionPriceCents: number = DEFAULT_SUBSCRIPTION_PRICE_CENTS,
): RingState {
  const safePrice = subscriptionPriceCents > 0
    ? subscriptionPriceCents
    : DEFAULT_SUBSCRIPTION_PRICE_CENTS;
  const totalAbonnements = totalSavingsCents / safePrice;
  const completedRings = Math.floor(totalAbonnements);
  const currentFill = totalAbonnements - completedRings;
  const prestigeLevel = Math.floor(completedRings / 10);
  const displayFossils = Math.min(completedRings, MAX_DISPLAY_FOSSILS);

  return {
    totalAbonnements,
    completedRings,
    currentFill,
    prestigeLevel,
    displayFossils,
  };
}

/**
 * Opacity for a fossil ring.
 * index 0 = most recent (closest to active ring), opacity 0.7
 * index totalFossils-1 = oldest, opacity 0.3
 */
export function getFossilOpacity(index: number, totalFossils: number): number {
  if (totalFossils <= 1) return 0.7;
  return 0.7 - (index / (totalFossils - 1)) * 0.4;
}

/**
 * Couleur affichée pour l'anneau de rang N (0-indexed).
 * Progression cyan → violet. Cycle après 10 anneaux (prestige).
 */
export const RING_COLORS = [
  '#22D3EE', // 1: cyan
  '#2DD4BF', // 2: teal
  '#34D399', // 3: emerald
  '#A3E635', // 4: lime
  '#FACC15', // 5: yellow
  '#FBBF24', // 6: amber
  '#F97316', // 7: orange
  '#EF4444', // 8: red
  '#EC4899', // 9: pink
  '#A855F7', // 10: violet (→ prestige)
] as const;

export function getRingColor(ringIndex: number): string {
  if (ringIndex < 0) return RING_COLORS[0];
  return RING_COLORS[ringIndex % RING_COLORS.length];
}
