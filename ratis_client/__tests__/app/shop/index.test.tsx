// ratis_client/__tests__/app/shop/index.test.tsx
//
// Boutique V1 — catalog screen rendering & navigation.

import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const mockCatalogState = {
  data: undefined as { brands: { id: string; name: string; logo_url: string | null; is_active: boolean }[] } | undefined,
  isLoading: false,
  isError: false,
  isSuccess: false,
  refetch: jest.fn(),
};
jest.mock('@/hooks/use-shop-catalog', () => ({
  useShopCatalog: () => mockCatalogState,
}));

const mockBalanceState = {
  balance: 47500,
  battlepass: null,
  isLoading: false,
  isError: false,
};
jest.mock('@/hooks/use-cab-balance', () => ({
  useCabBalance: () => mockBalanceState,
}));

import ShopCatalogScreen from '@/app/shop';

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
  mockCatalogState.data = undefined;
  mockCatalogState.isLoading = false;
  mockCatalogState.isError = false;
  mockCatalogState.isSuccess = false;
  mockBalanceState.balance = 47500;
});

const BRANDS = [
  {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'Amazon.fr',
    logo_url: 'https://example.com/amazon.png',
    is_active: true,
  },
  {
    id: '22222222-2222-2222-2222-222222222222',
    name: 'Carrefour',
    logo_url: null,
    is_active: true,
  },
];

describe('ShopCatalogScreen', () => {
  it('renders the loading state while the catalog fetches', () => {
    mockCatalogState.isLoading = true;
    const { getByTestId } = renderWithQuery(<ShopCatalogScreen />);
    expect(getByTestId('shop-catalog-loading')).toBeTruthy();
  });

  it('renders the brand grid + balance once loaded', async () => {
    mockCatalogState.isSuccess = true;
    mockCatalogState.data = { brands: BRANDS };
    const { getByTestId, findByText } = renderWithQuery(<ShopCatalogScreen />);
    await waitFor(() => expect(getByTestId('shop-catalog-grid')).toBeTruthy());
    expect(await findByText(/Amazon.fr/)).toBeTruthy();
    expect(await findByText(/Carrefour/)).toBeTruthy();
    expect(getByTestId('shop-balance')).toBeTruthy();
  });

  it('renders an empty state when the catalog has no brands', () => {
    mockCatalogState.isSuccess = true;
    mockCatalogState.data = { brands: [] };
    const { getByTestId } = renderWithQuery(<ShopCatalogScreen />);
    expect(getByTestId('shop-catalog-empty')).toBeTruthy();
  });

  it('renders an error state with retry button on failure', () => {
    mockCatalogState.isError = true;
    const { getByTestId } = renderWithQuery(<ShopCatalogScreen />);
    expect(getByTestId('shop-catalog-error')).toBeTruthy();
    fireEvent.press(getByTestId('shop-catalog-retry'));
    expect(mockCatalogState.refetch).toHaveBeenCalledTimes(1);
  });

  it('navigates to the brand denomination screen on tile press', () => {
    mockCatalogState.isSuccess = true;
    mockCatalogState.data = { brands: BRANDS };
    const { getByTestId } = renderWithQuery(<ShopCatalogScreen />);
    fireEvent.press(getByTestId(`shop-brand-${BRANDS[0].id}`));
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/shop/[brand_id]',
      params: { brand_id: BRANDS[0].id },
    });
  });

  it('back button calls router.back()', () => {
    mockCatalogState.isSuccess = true;
    mockCatalogState.data = { brands: [] };
    const { getByTestId } = renderWithQuery(<ShopCatalogScreen />);
    fireEvent.press(getByTestId('shop-back'));
    expect(mockBack).toHaveBeenCalled();
  });
});
