// __tests__/app/(tabs)/produit.test.tsx
//
// Restored at chunk 5 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Adapted from the V4 test (commit 932c065^) — the V5 screen drops the
// AppHeader on the Produit detail surface (matches JSX iso) and reads `ean`
// from the route param without a hardcoded fallback. Loading/error/empty
// states + back/favorite/share/CTA wirings are covered.

import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('@/components/ui/screen-background', () => ({
  ScreenBackground: () => null,
}));
jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});
jest.mock('react-native-svg', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  const Pass = ({
    children,
    ...props
  }: {
    children?: React.ReactNode;
    [k: string]: unknown;
  }) => RnReact.createElement(RN.View, props, children);
  return {
    __esModule: true,
    default: Pass,
    Svg: Pass,
    Path: () => null,
    Circle: () => null,
    Rect: () => null,
    Line: () => null,
    G: Pass,
    Defs: Pass,
    LinearGradient: Pass,
    RadialGradient: Pass,
    Stop: () => null,
    Polygon: () => null,
    Polyline: () => null,
    Text: ({ children }: { children?: React.ReactNode }) =>
      RnReact.createElement(RnReact.Fragment, null, children),
  };
});
jest.mock('react-native-safe-area-context', () => {
  const RnReact = require('react');
  return {
    SafeAreaView: ({ children }: { children?: React.ReactNode }) =>
      RnReact.createElement(RnReact.Fragment, null, children),
    useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  };
});

const mockFavMutate = jest.fn();
const mockIsFavorite = jest.fn<boolean, []>(() => false);
jest.mock('@/hooks/use-favorites', () => ({
  useIsFavorite: () => mockIsFavorite(),
  useToggleFavorite: () => ({ mutate: mockFavMutate, isPending: false }),
}));

const mockShare = jest.fn().mockResolvedValue({ action: 'sharedAction' });
jest.mock('react-native/Libraries/Share/Share', () => ({
  __esModule: true,
  default: { share: (...a: unknown[]) => mockShare(...a) },
}));

const mockRequestPerms = jest.fn();
const mockGetPos = jest.fn();
jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: (...a: unknown[]) =>
    mockRequestPerms(...a),
  getCurrentPositionAsync: (...a: unknown[]) => mockGetPos(...a),
  Accuracy: { Balanced: 3 },
}));

const mockUseProductByEan = jest.fn();
jest.mock('@/hooks/use-product-by-ean', () => ({
  useProductByEan: (...args: unknown[]) => mockUseProductByEan(...args),
}));

const mockUseProductSearch = jest.fn();
jest.mock('@/hooks/use-product-search', () => ({
  useProductSearch: (...args: unknown[]) => mockUseProductSearch(...args),
}));

// Wave-13 (PO ticket 2026-05-14) — screen-mount prefetch of the
// default-suggestions cache so the empty-state of the search field
// renders instantly when the user focuses it.
const mockUseDefaultSuggestions = jest.fn(() => ({
  data: { items: [] },
  isLoading: false,
  isFetching: false,
  isSuccess: true,
}));
jest.mock('@/hooks/use-default-suggestions', () => ({
  useDefaultSuggestions: (...args: unknown[]) =>
    mockUseDefaultSuggestions(...(args as [])),
}));

const mockRouterBack = jest.fn();
const mockRouterPush = jest.fn();
const mockRouterReplace = jest.fn();
const mockUseLocalSearchParams = jest.fn<{ ean?: string }, []>(() => ({
  ean: '3428270000019',
}));
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockUseLocalSearchParams(),
  router: {
    push: (...a: unknown[]) => mockRouterPush(...a),
    back: (...a: unknown[]) => mockRouterBack(...a),
    replace: (...a: unknown[]) => mockRouterReplace(...a),
  },
}));

import ProduitScreen from '@/app/(tabs)/produit';

const PRODUCT_DATA = {
  product: {
    ean: '3428270000019',
    name: 'Lait demi-écrémé 1L',
    brand: 'Lactel',
    photo_url: null,
    storage_type: 'refrigerated',
    product_quantity: 1.0,
    product_quantity_unit: 'L',
  },
  local_price: null,
  // price_cents is an integer number of cents (int-cents) — 119 ⇒ "1,19€".
  nearby_prices: [
    {
      store_id: 's1',
      store_name: 'Leclerc Parmentier',
      price_cents: 119,
      distance_km: 1.2,
    },
    {
      store_id: 's2',
      store_name: 'Monoprix République',
      price_cents: 129,
      distance_km: 0.8,
    },
  ],
};

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('ProduitScreen (V5 strict iso)', () => {
  beforeEach(() => {
    mockRequestPerms.mockReset();
    mockGetPos.mockReset();
    mockUseProductByEan.mockReset();
    mockFavMutate.mockClear();
    mockRouterBack.mockClear();
    mockRouterPush.mockClear();
    mockShare.mockClear();
    mockIsFavorite.mockReset();
    mockIsFavorite.mockReturnValue(false);
    mockUseLocalSearchParams.mockReturnValue({ ean: '3428270000019' });
    mockRequestPerms.mockResolvedValue({ status: 'granted' });
    mockGetPos.mockResolvedValue({
      coords: { latitude: 48.86, longitude: 2.34 },
    });
    mockUseProductSearch.mockReset();
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
  });

  it('renders product info once data is loaded', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByText } = renderWithQuery(<ProduitScreen />);
    await waitFor(() => expect(getByText('Lait demi-écrémé 1L')).toBeTruthy());
    expect(getByText('LACTEL')).toBeTruthy();
    expect(getByText('3428270000019')).toBeTruthy();
  });

  // Wave-13 (PO ticket 2026-05-14) — screen-mount prefetch of the
  // default-suggestions cache so the search empty-state on the
  // Produit tab (when no ?ean= param) renders instantly when the
  // user focuses the search field. RQ dedupes by queryKey.
  it('warms the default suggestions cache at mount', () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    mockUseDefaultSuggestions.mockClear();
    renderWithQuery(<ProduitScreen />);
    expect(mockUseDefaultSuggestions).toHaveBeenCalled();
  });

  it('renders best nearby price as consensus and sorted price rows', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByText, getAllByText } = renderWithQuery(<ProduitScreen />);
    await waitFor(() =>
      expect(getAllByText('1,19€').length).toBeGreaterThanOrEqual(1),
    );
    expect(getByText('Leclerc Parmentier')).toBeTruthy();
    expect(getByText('Monoprix République')).toBeTruthy();
    expect(getByText(/2 magasins à proximité/)).toBeTruthy();
  });

  it('consumes price_cents directly as int-cents (no float-euro * 100)', async () => {
    // Regression — the backend exposes an integer price_cents. The old code
    // did `Math.round(price * 100)` treating the value as euros, which on a
    // cents value (450) yielded 45000 cents ⇒ "450,00€". With the fix the
    // 450-cent value renders "4,50€".
    mockUseProductByEan.mockReturnValue({
      data: {
        ...PRODUCT_DATA,
        nearby_prices: [
          {
            store_id: 's1',
            store_name: 'Auchan Nation',
            price_cents: 450,
            distance_km: 2.8,
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getAllByText, queryByText } = renderWithQuery(<ProduitScreen />);
    await waitFor(() =>
      expect(getAllByText('4,50€').length).toBeGreaterThanOrEqual(1),
    );
    // The pre-fix bug would have rendered "450,00€".
    expect(queryByText('450,00€')).toBeNull();
  });

  it('displays best store with MEILLEUR label and other with delta %', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByText, findByText } = renderWithQuery(<ProduitScreen />);
    expect(await findByText('Leclerc Parmentier')).toBeTruthy();
    expect(getByText('MEILLEUR')).toBeTruthy();
    // 1.29 vs 1.19 → ((1.29-1.19)/1.19)*100 ≈ 8.4 → round 8
    expect(getByText('+8%')).toBeTruthy();
  });

  it('renders loading state when data is pending', () => {
    mockUseProductByEan.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    expect(getByTestId('produit-loading')).toBeTruthy();
  });

  it('renders error state when product is not found', () => {
    mockUseProductByEan.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: jest.fn(),
    });
    const { getByText } = renderWithQuery(<ProduitScreen />);
    expect(getByText(/Produit introuvable/i)).toBeTruthy();
  });

  it('shows location hint when permission denied and no prices', async () => {
    mockRequestPerms.mockResolvedValue({ status: 'denied' });
    mockUseProductByEan.mockReturnValue({
      data: { ...PRODUCT_DATA, nearby_prices: [] },
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { findAllByText } = renderWithQuery(<ProduitScreen />);
    const matches = await findAllByText(/Active la géoloc pour voir les prix/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('renders the LocationPermissionBanner when permission is denied', async () => {
    mockRequestPerms.mockResolvedValue({ status: 'denied' });
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { findByTestId } = renderWithQuery(<ProduitScreen />);
    expect(await findByTestId('location-permission-banner')).toBeTruthy();
  });

  it('back button in header navigates back via router', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    await waitFor(() => expect(getByTestId('btn-back')).toBeTruthy());
    fireEvent.press(getByTestId('btn-back'));
    expect(mockRouterBack).toHaveBeenCalledTimes(1);
  });

  it('wires favorite button to toggleFavorite with current ean', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    await waitFor(() => expect(getByTestId('btn-favorite')).toBeTruthy());
    fireEvent.press(getByTestId('btn-favorite'));
    expect(mockFavMutate).toHaveBeenCalledWith({
      ean: '3428270000019',
      favorited: true,
    });
  });

  it('share button triggers React Native Share with product name', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    await waitFor(() => expect(getByTestId('btn-share')).toBeTruthy());
    fireEvent.press(getByTestId('btn-share'));
    expect(mockShare).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining('Lait demi-écrémé 1L'),
      }),
    );
  });

  it('renders sticky "Ajouter à ma liste" CTA when product loaded', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    await waitFor(() => expect(getByTestId('btn-add-to-list')).toBeTruthy());
  });

  it('uses "Fiche produit" as page title (V5 iso)', async () => {
    mockUseProductByEan.mockReturnValue({
      data: PRODUCT_DATA,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByText } = renderWithQuery(<ProduitScreen />);
    expect(getByText('Fiche produit')).toBeTruthy();
  });

  // Wave-4 Bug 4 — the empty state now hosts the product search box.
  // The legacy « Aucun produit sélectionné » heading was replaced by
  // the wave-4 search title ; the testID `produit-empty` is kept so
  // existing back-pointers keep working.
  it('renders the search empty-state when no ean route param is present', () => {
    mockUseLocalSearchParams.mockReturnValue({});
    mockUseProductByEan.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    const { getByTestId, getByText } = renderWithQuery(<ProduitScreen />);
    expect(getByTestId('produit-empty')).toBeTruthy();
    expect(getByText(/Chercher un produit/i)).toBeTruthy();
    expect(getByTestId('produit-search-bar')).toBeTruthy();
  });
});

// Wave-4 Bug 4 — Produit tab text-search empty state.
describe('ProduitScreen — wave-4 search empty state (Bug 4)', () => {
  const HIT = {
    ean: '3017620420001',
    name: 'Lait demi-écrémé 1L',
    brands: 'Lactel',
    quantity: null,
    categories_tags: null,
    labels_tags: null,
    origins_tags: null,
    source: 'off',
  };

  beforeEach(() => {
    mockUseLocalSearchParams.mockReset();
    mockUseLocalSearchParams.mockReturnValue({});
    mockUseProductByEan.mockReset();
    mockUseProductByEan.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      refetch: jest.fn(),
    });
    mockUseProductSearch.mockReset();
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    mockRouterPush.mockClear();
    mockRouterBack.mockClear();
  });

  it('renders the search input', () => {
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    expect(getByTestId('produit-search-input')).toBeTruthy();
  });

  it('hides the dropdown while the query is below the min length', () => {
    const { getByTestId, queryByTestId } = renderWithQuery(<ProduitScreen />);
    const input = getByTestId('produit-search-input');
    fireEvent.changeText(input, 'l');
    expect(queryByTestId('produit-search-dropdown')).toBeNull();
  });

  it('renders search hits once the query has ≥2 chars', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    const input = getByTestId('produit-search-input');
    fireEvent.changeText(input, 'lait');
    expect(getByTestId('produit-search-dropdown')).toBeTruthy();
    expect(getByTestId(`produit-search-hit-${HIT.ean}`)).toBeTruthy();
  });

  it('renders the no-result row when search returns nothing', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [] },
      isFetching: false,
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    const input = getByTestId('produit-search-input');
    fireEvent.changeText(input, 'xyz');
    expect(getByTestId('produit-search-empty')).toBeTruthy();
  });

  // Wave-4 Bug 4 — tap a hit → ``router.replace`` to the same tab with
  // the resolved ``?ean=``. We use ``replace`` (not ``push``) so the
  // empty-state is not kept in the navigation back-stack (« tap back »
  // from the detail screen would otherwise land on an empty input).
  it('navigates to the same tab with ?ean= on tap, via replace (no back-stack pollution)', () => {
    mockUseProductSearch.mockReturnValue({
      data: { items: [HIT] },
      isFetching: false,
    });
    mockRouterReplace.mockClear();
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    const input = getByTestId('produit-search-input');
    fireEvent.changeText(input, 'lait');
    fireEvent.press(getByTestId(`produit-search-hit-${HIT.ean}`));
    expect(mockRouterReplace).toHaveBeenCalledWith(
      expect.objectContaining({
        params: { ean: HIT.ean },
      }),
    );
  });

  // Wave 9 — enriched secondary line on the Produit-tab dropdown (same
  // composition rule as the Liste AddBar, see
  // ``components/liste/add-bar.tsx`` and ``utils/product-search-hit.ts``).
  it('renders the enriched secondary line « brand · quantity · 🇫🇷 · 🌱 »', () => {
    const RICH_HIT = {
      ean: '3017620421006',
      name: 'Pomme de terre bio',
      brands: 'Bio Village',
      quantity: '500 g',
      categories_tags: null,
      labels_tags: ['en:organic'],
      origins_tags: ['en:france'],
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [RICH_HIT] },
      isFetching: false,
    });
    const { getByTestId } = renderWithQuery(<ProduitScreen />);
    const input = getByTestId('produit-search-input');
    fireEvent.changeText(input, 'pomme');
    const secondary = getByTestId(
      `produit-search-hit-${RICH_HIT.ean}-secondary`,
    );
    expect(secondary.props.children).toBe(
      'Bio Village · 500 g · 🇫🇷 · 🌱',
    );
  });

  it('hides the secondary line when brand / quantity / origins / labels are all null', () => {
    const NAKED_HIT = {
      ean: '3017620421001',
      name: 'Pomme de terre',
      brands: null,
      quantity: null,
      categories_tags: null,
      labels_tags: null,
      origins_tags: null,
      source: 'off',
    };
    mockUseProductSearch.mockReturnValue({
      data: { items: [NAKED_HIT] },
      isFetching: false,
    });
    const { getByTestId, queryByTestId } = renderWithQuery(<ProduitScreen />);
    const input = getByTestId('produit-search-input');
    fireEvent.changeText(input, 'pomme');
    expect(getByTestId(`produit-search-hit-${NAKED_HIT.ean}`)).toBeTruthy();
    expect(
      queryByTestId(`produit-search-hit-${NAKED_HIT.ean}-secondary`),
    ).toBeNull();
  });
});
