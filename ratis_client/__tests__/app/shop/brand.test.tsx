// ratis_client/__tests__/app/shop/brand.test.tsx
//
// Boutique V1 — brand denominations screen + confirm modal.

import React from 'react';
import {
  render,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react-native';
import { Alert } from 'react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
  useLocalSearchParams: () => ({ brand_id: BRAND_ID }),
}));

// ── Hook mocks ───────────────────────────────────────────────────────────────
const BRAND_ID = '11111111-1111-1111-1111-111111111111';

const mockCatalogState = {
  data: {
    brands: [
      {
        id: BRAND_ID,
        name: 'Amazon.fr',
        logo_url: null,
        is_active: true,
      },
    ],
  },
  isLoading: false,
  isError: false,
  isSuccess: true,
};
jest.mock('@/hooks/use-shop-catalog', () => ({
  useShopCatalog: () => mockCatalogState,
}));

// V1.1 — `useGiftCards`/`computeUsageStats` no longer consumed by the
// shop screen. The cap-usage line is sourced from `useGiftCardCapUsage`
// (server) and the per-brand history line from `useShopUsageStats`.
const mockCapUsageState = {
  data: {
    year: 2026,
    ytd_cents: 0,
    annual_warning_threshold_cents: 30500,
    annual_hard_cap_cents: 119900,
    remaining_cents: 119900,
    warning_threshold_reached: false,
    daily_cents: 0,
    weekly_cents: 0,
    daily_cap_cents: 10_000,
    weekly_cap_cents: 30_000,
  },
  isLoading: false,
  isError: false,
  isSuccess: true,
};
jest.mock('@/hooks/use-gift-card-cap-usage', () => ({
  useGiftCardCapUsage: () => mockCapUsageState,
}));

const mockBrandStatsState = {
  data: {
    brand_id: BRAND_ID,
    orders_count: 0,
    total_saved_cents: 0,
    first_order_at: null,
    last_order_at: null,
  },
  isLoading: false,
  isError: false,
  isSuccess: true,
};
jest.mock('@/hooks/use-shop-usage-stats', () => ({
  useShopUsageStats: () => mockBrandStatsState,
}));

const mockBalanceState = {
  balance: 200_000,
  battlepass: null,
  isLoading: false,
  isError: false,
};
jest.mock('@/hooks/use-cab-balance', () => ({
  useCabBalance: () => mockBalanceState,
}));

const mockMutateAsync = jest.fn();
const mockOrderState = {
  mutateAsync: mockMutateAsync,
  isPending: false,
};
jest.mock('@/hooks/use-shop-order', () => ({
  useShopOrder: () => mockOrderState,
}));

// ── Imports after mocks ──────────────────────────────────────────────────────
import ShopBrandScreen from '@/app/shop/[brand_id]';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockCapUsageState.data.daily_cents = 0;
  mockCapUsageState.data.weekly_cents = 0;
  mockBrandStatsState.data.orders_count = 0;
  mockBrandStatsState.data.total_saved_cents = 0;
  mockBalanceState.balance = 200_000;
  mockOrderState.isPending = false;
});

describe('ShopBrandScreen — denominations + caps', () => {
  it('renders all 4 V1 denominations', () => {
    const { getByTestId } = renderWithQuery(<ShopBrandScreen />);
    expect(getByTestId('shop-denom-500')).toBeTruthy();
    expect(getByTestId('shop-denom-1000')).toBeTruthy();
    expect(getByTestId('shop-denom-2000')).toBeTruthy();
    expect(getByTestId('shop-denom-5000')).toBeTruthy();
  });

  it('disables denominations the user cannot afford', () => {
    mockBalanceState.balance = 60_000; // can afford 5€ (25k) and 10€ (50k), not 20€/50€
    const { getByTestId } = renderWithQuery(<ShopBrandScreen />);
    const row20 = getByTestId('shop-denom-2000');
    const row50 = getByTestId('shop-denom-5000');
    expect(row20.props.accessibilityState).toEqual({ disabled: true });
    expect(row50.props.accessibilityState).toEqual({ disabled: true });
  });

  it('shows daily/weekly caps line with the MVP note', () => {
    const { getByTestId } = renderWithQuery(<ShopBrandScreen />);
    const caps = getByTestId('shop-caps');
    expect(caps).toBeTruthy();
    expect(getByTestId('shop-caps-mvp-note')).toBeTruthy();
  });

  it('opens the confirm modal on denomination press', () => {
    const { getByTestId, queryByTestId } = renderWithQuery(<ShopBrandScreen />);
    expect(queryByTestId('shop-confirm-submit')).toBeNull();
    fireEvent.press(getByTestId('shop-denom-2000'));
    expect(getByTestId('shop-confirm-submit')).toBeTruthy();
  });

  it('cancels the modal without calling the API', () => {
    const { getByTestId } = renderWithQuery(<ShopBrandScreen />);
    fireEvent.press(getByTestId('shop-denom-2000'));
    fireEvent.press(getByTestId('shop-confirm-cancel'));
    expect(mockMutateAsync).not.toHaveBeenCalled();
  });

  it('confirms the purchase and routes to profil on success', async () => {
    const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
    mockMutateAsync.mockResolvedValue({
      order_id: 'o1',
      brand: 'Amazon.fr',
      denomination_cents: 2000,
      cab_cost: 100_000,
      new_cab_balance: 100_000,
      status: 'pending',
      estimated_arrival: 'in a few seconds',
    });

    const { getByTestId } = renderWithQuery(<ShopBrandScreen />);
    fireEvent.press(getByTestId('shop-denom-2000'));
    await act(async () => {
      fireEvent.press(getByTestId('shop-confirm-submit'));
    });

    await waitFor(() =>
      expect(mockMutateAsync).toHaveBeenCalledWith({
        brand_id: BRAND_ID,
        denomination_cents: 2000,
      }),
    );
    expect(alertSpy).toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it('surfaces the i18n error message when the API rejects with a known code', async () => {
    const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
    mockMutateAsync.mockRejectedValue(
      Object.assign(new Error('insufficient_cab_balance'), {
        code: 'insufficient_cab_balance',
      }),
    );
    const { getByTestId } = renderWithQuery(<ShopBrandScreen />);
    fireEvent.press(getByTestId('shop-denom-500'));
    await act(async () => {
      fireEvent.press(getByTestId('shop-confirm-submit'));
    });
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    // Error path → first arg of alert is shop.error_title (translated by
    // i18next mock to the key fallback or the raw text).
    alertSpy.mockRestore();
  });
});
