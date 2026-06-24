// ratis_client/hooks/use-gift-cards.ts
//
// Boutique V1 — fetch the user's gift-card orders.
//
// Used by:
//   - the future "Mes cartes cadeaux" screen
//   - the brand denominations screen, to compute the daily / weekly redeem
//     caps client-side until the backend ships a dedicated stats endpoint
//     (V1.x — see ARCH_boutique.md § Hors scope V1).

import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';

export interface GiftCardOrder {
  id: string;
  denomination: number;
  status: 'pending' | 'issued' | 'failed' | 'churned';
  source_type: 'shop_purchase' | 'annual_subscription' | 'referral';
  code: string | null;
  issued_at: string | null;
  failed_at: string | null;
  created_at: string | null;
  brand: {
    id: string;
    name: string;
    logo_url: string | null;
  };
}

export function useGiftCards() {
  return useQuery<GiftCardOrder[]>({
    queryKey: ['gift-cards'],
    queryFn: () => rewardsClient.get<GiftCardOrder[]>('/rewards/gift-cards'),
  });
}

// -----------------------------------------------------------------------------
// Cap usage helpers — DEPRECATED.
//
// V1.1 ships a server-authoritative endpoint (`useGiftCardCapUsage()` →
// `GET /rewards/gift-cards/cap-usage`) that supersedes the helpers below.
// They are kept exported for one release cycle so external (test-only)
// consumers don't break ; remove in V1.2 once no callsite imports them.
// Production code paths must NOT call `computeUsageStats` anymore.
// -----------------------------------------------------------------------------

/** Returns true when the ISO timestamp falls on the same calendar day in
 *  Europe/Paris as the reference Date (default `new Date()`).
 *
 *  We use `Intl.DateTimeFormat` so the comparison is timezone-correct without
 *  pulling moment/luxon. The backend reads `Europe/Paris` for cap windows
 *  (cf ARCH_boutique.md § Caps), so the client must match. */
function sameParisDay(iso: string, ref: Date = new Date()): boolean {
  return parisDateStr(new Date(iso)) === parisDateStr(ref);
}

/** ISO week start (Monday) in Europe/Paris. We can't easily get "week" via
 *  Intl ; compute via date math after shifting through the formatter. */
function parisDateStr(d: Date): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Paris',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(d);
}

function startOfParisWeek(ref: Date = new Date()): Date {
  // Convert ref into a Paris-local calendar Y/M/D, then compute the Monday
  // of that calendar week. We carry the time at midnight Paris.
  const [y, m, d] = parisDateStr(ref).split('-').map((s) => Number.parseInt(s, 10));
  // Build a UTC date AT Paris midnight by going through a parsed string the
  // browser interprets as the start of the day in the local TZ. To avoid
  // ambiguity we use the `T00:00:00` Paris offset via a probing trick:
  // we just reconstruct a UTC midnight and compare day-of-week through the
  // same formatter.
  const probe = new Date(Date.UTC(y, m - 1, d));
  // What weekday is `probe` in Paris? Use the formatter.
  const weekdayName = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Europe/Paris',
    weekday: 'short',
  }).format(probe);
  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const idx = WEEKDAYS.indexOf(weekdayName);
  // ISO week starts on Monday (idx=1). Days to subtract = (idx + 6) % 7.
  const toMonday = (idx + 6) % 7;
  const monday = new Date(probe);
  monday.setUTCDate(probe.getUTCDate() - toMonday);
  return monday;
}

export interface ShopUsageStats {
  /** Cents redeemed today via shop_purchase (Europe/Paris day window). */
  daily_cents: number;
  /** Cents redeemed this ISO week (Monday Paris) via shop_purchase. */
  weekly_cents: number;
}

/** @deprecated V1.1 — use `useGiftCardCapUsage()` (server-authoritative).
 *  This client-side reducer breaks once `GET /rewards/gift-cards`
 *  paginates ; the new endpoint computes the aggregate via SQL.
 *
 *  Compute daily + weekly shop-purchase cents from the gift-cards list.
 *  Filters to `source_type='shop_purchase'` and excludes `status='failed'`
 *  (failed orders shouldn't consume the user's cap). */
export function computeUsageStats(
  orders: GiftCardOrder[] | undefined,
  now: Date = new Date(),
): ShopUsageStats {
  if (!orders || orders.length === 0) {
    return { daily_cents: 0, weekly_cents: 0 };
  }

  const weekStart = startOfParisWeek(now);
  let daily = 0;
  let weekly = 0;

  for (const o of orders) {
    if (o.source_type !== 'shop_purchase') continue;
    if (o.status === 'failed') continue;
    if (!o.created_at) continue;

    const created = new Date(o.created_at);
    if (sameParisDay(o.created_at, now)) {
      daily += o.denomination;
    }
    if (created >= weekStart) {
      weekly += o.denomination;
    }
  }

  return { daily_cents: daily, weekly_cents: weekly };
}
