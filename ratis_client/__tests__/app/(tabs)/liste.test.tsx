// __tests__/app/(tabs)/liste.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Smoke test only — the V5 tab surfaces a different testID anatomy than the
// V4 ancestor, so we don't restore the V4 assertions verbatim. We confirm:
//   - it mounts under a QueryClientProvider without crashing
//   - the page title "Ma liste" is visible
//   - the segmented tabs are wired and the products tab is active by default
//   - the products empty state renders when there are no items

import React from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  waitFor,
} from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthError } from '@/types/auth';

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
jest.mock('react-native-safe-area-context', () => {
  const RnReact = require('react');
  return {
    SafeAreaView: ({ children }: { children: React.ReactNode }) =>
      RnReact.createElement(RnReact.Fragment, null, children),
    useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  };
});
jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({
    status: 'granted',
  }),
  getCurrentPositionAsync: jest
    .fn()
    .mockResolvedValue({ coords: { latitude: 0, longitude: 0 } }),
}));
jest.mock('@/services/list-client', () => ({
  listClient: {
    get: jest.fn().mockResolvedValue([]),
    post: jest.fn().mockResolvedValue({}),
    patch: jest.fn().mockResolvedValue({}),
    delete: jest.fn().mockResolvedValue(undefined),
  },
}));
jest.mock('@/services/api-client', () => ({
  apiClient: {
    get: jest.fn().mockResolvedValue(null),
    post: jest.fn().mockResolvedValue({}),
  },
  // ``createApiClient`` is consumed by ``services/product-client.ts``
  // which is imported transitively through the new
  // ``useProductSearch`` hook (Bug 3 wave 4). Returning a stub keeps
  // the import chain happy without making real network calls.
  createApiClient: () => ({
    get: jest.fn().mockResolvedValue({ items: [] }),
    post: jest.fn().mockResolvedValue({}),
    patch: jest.fn().mockResolvedValue({}),
    delete: jest.fn().mockResolvedValue(undefined),
  }),
}));
// Wave-4 AddBar import surface — the screen pulls in
// ``useProductSearch`` indirectly. Stub it to a no-op idle state so
// the dropdown stays hidden in every Liste-tab assertion. The
// AddItem-toast suite below overrides this per-test via
// ``mockImplementation`` to surface a hit and drive the mutation.
jest.mock('@/hooks/use-product-search', () => ({
  useProductSearch: jest.fn(() => ({
    data: { items: [] },
    isFetching: false,
  })),
}));
// Wave-13 (PO ticket 2026-05-14) — screen-mount prefetch of the
// default-suggestions cache. Stub returns an idle success so the
// React Query call is a no-op in tests ; spec assertion below
// checks the hook is called at all.
jest.mock('@/hooks/use-default-suggestions', () => ({
  useDefaultSuggestions: jest.fn(() => ({
    data: { items: [] },
    isLoading: false,
    isFetching: false,
    isSuccess: true,
  })),
}));

// Mock the shopping-list hooks so ``listId`` is set
// synchronously — without this, the React Query fetch (resolved on
// a microtask) can race the dropdown-press in parallel jest workers
// and the AddItem mutation no-ops because listId is still null.
// Concrete cases below override the ``useActiveList`` return.
jest.mock('@/hooks/use-shopping-lists', () => ({
  useActiveList: jest.fn(() => ({ data: null })),
  useShoppingLists: jest.fn(() => ({ data: [], isLoading: false })),
  useCreateList: jest.fn(() => ({
    mutateAsync: jest.fn(async () => ({
      id: 'fresh-list-id',
      name: 'Ma liste',
    })),
  })),
}));
jest.mock('@/hooks/use-shopping-list-detail', () => ({
  useShoppingListDetail: jest.fn(() => ({ data: null, isLoading: false })),
}));
jest.mock('@/hooks/use-active-route', () => ({
  useActiveRoute: jest.fn(() => ({ data: null })),
}));
jest.mock('@/hooks/use-optimize-route', () => ({
  useOptimizeRoute: jest.fn(() => ({ mutate: jest.fn(), isPending: false })),
}));

import ListeScreen from '@/app/(tabs)/liste';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      // ``retry: false`` on mutations too so a rejected ``mutate``
      // surfaces ``onError`` immediately (the AddItem-toast suite
      // below mocks 4xx/5xx responses and relies on prompt feedback).
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('ListeScreen (V5)', () => {
  it('renders without crash', () => {
    const { toJSON } = renderWithQuery(<ListeScreen />);
    expect(toJSON()).not.toBeNull();
  });

  it('renders the page title', () => {
    const { getByText } = renderWithQuery(<ListeScreen />);
    expect(getByText('Ma liste')).toBeTruthy();
  });

  it('renders the segmented tabs', () => {
    const { getByTestId } = renderWithQuery(<ListeScreen />);
    expect(getByTestId('liste-tabs')).toBeTruthy();
    expect(getByTestId('liste-tabs-tab-products')).toBeTruthy();
    expect(getByTestId('liste-tabs-tab-route')).toBeTruthy();
  });

  // Wave-13 (PO ticket 2026-05-14) — the screen now warms the
  // default-suggestions cache at mount so the AddBar dropdown
  // renders instantly when the user focuses the empty search field.
  it('warms the default suggestions cache at mount', () => {
    /* eslint-disable @typescript-eslint/no-require-imports */
    const useDefaultSuggestionsMock = require(
      '@/hooks/use-default-suggestions',
    ).useDefaultSuggestions as jest.Mock;
    /* eslint-enable @typescript-eslint/no-require-imports */
    useDefaultSuggestionsMock.mockClear();
    renderWithQuery(<ListeScreen />);
    expect(useDefaultSuggestionsMock).toHaveBeenCalled();
  });
});

// Wave 12 (PO ticket 2026-05-14) — items grouped by category section
// + market.svg watermark behind the rows.
describe('ListeScreen — wave-12 category grouping + bg', () => {
  /* eslint-disable @typescript-eslint/no-require-imports */
  const useShoppingListDetailMock = require('@/hooks/use-shopping-list-detail')
    .useShoppingListDetail as jest.Mock;
  const useActiveListMock = require('@/hooks/use-shopping-lists')
    .useActiveList as jest.Mock;
  /* eslint-enable @typescript-eslint/no-require-imports */

  function makeItem(
    overrides: Partial<{
      id: string;
      product_ean: string;
      product_name: string;
      category: string | null;
    }>,
  ) {
    return {
      id: overrides.id ?? 'i-' + (overrides.product_ean ?? '0'),
      product_ean: overrides.product_ean ?? '3017620499000',
      product_name: overrides.product_name ?? 'Test',
      quantity: 1,
      checked: false,
      checked_at: null,
      category: overrides.category ?? null,
    };
  }

  beforeEach(() => {
    cleanup();
    useActiveListMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: '',
        has_default_name: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    }));
  });

  afterAll(() => {
    useActiveListMock.mockImplementation(() => ({ data: null }));
    useShoppingListDetailMock.mockImplementation(() => ({
      data: null,
      isLoading: false,
    }));
  });

  it('groups items into category sections in the canonical order', () => {
    useShoppingListDetailMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: null,
        has_default_name: true,
        is_template: false,
        items: [
          // Intentionally jumbled order to confirm grouping reshuffles.
          makeItem({
            product_ean: '1',
            product_name: 'Eau Evian',
            category: 'boissons',
          }),
          makeItem({
            product_ean: '2',
            product_name: 'Yaourt nature',
            category: 'frais',
          }),
          makeItem({
            product_ean: '3',
            product_name: 'Baguette',
            category: 'boulangerie',
          }),
          makeItem({
            product_ean: '4',
            product_name: 'Pâtes',
            category: 'epicerie',
          }),
        ],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      isLoading: false,
    }));
    const { getByTestId } = renderWithQuery(<ListeScreen />);
    // All four sections render.
    expect(getByTestId('liste-section-frais')).toBeTruthy();
    expect(getByTestId('liste-section-boulangerie')).toBeTruthy();
    expect(getByTestId('liste-section-epicerie')).toBeTruthy();
    expect(getByTestId('liste-section-boissons')).toBeTruthy();
    // Empty buckets are not rendered.
    expect(() => getByTestId('liste-section-vrac')).toThrow();
    expect(() => getByTestId('liste-section-autres')).toThrow();
  });

  it('renders the French category header label', () => {
    useShoppingListDetailMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: null,
        has_default_name: true,
        is_template: false,
        items: [
          makeItem({
            product_ean: '1',
            product_name: 'Yaourt',
            category: 'frais',
          }),
        ],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      isLoading: false,
    }));
    const { getByTestId } = renderWithQuery(<ListeScreen />);
    const header = getByTestId('liste-section-frais-header');
    expect(header.props.children).toBe('Frais alimentaire');
  });

  it('falls back to « Autres » for items with null category', () => {
    useShoppingListDetailMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: null,
        has_default_name: true,
        is_template: false,
        items: [
          makeItem({
            product_ean: '1',
            product_name: 'Truc inconnu',
            category: null,
          }),
        ],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      isLoading: false,
    }));
    const { getByTestId } = renderWithQuery(<ListeScreen />);
    expect(getByTestId('liste-section-autres')).toBeTruthy();
    expect(getByTestId('liste-section-autres-header').props.children).toBe(
      'Autres',
    );
  });

  it('renders the market.svg bg watermark behind the grouped items', () => {
    useShoppingListDetailMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: null,
        has_default_name: true,
        is_template: false,
        items: [
          makeItem({
            product_ean: '1',
            product_name: 'Pâtes',
            category: 'epicerie',
          }),
        ],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      isLoading: false,
    }));
    const { getByTestId } = renderWithQuery(<ListeScreen />);
    const bg = getByTestId('liste-market-bg');
    expect(bg).toBeTruthy();
    // pointerEvents: 'none' so the watermark never intercepts taps. RN
    // exposes it on ``props.pointerEvents``.
    expect(bg.props.pointerEvents).toBe('none');
  });

  it('preserves the orchestration block (grouped items wrapper) only when items exist', () => {
    useShoppingListDetailMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: null,
        has_default_name: true,
        is_template: false,
        items: [],
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      isLoading: false,
    }));
    const { queryByTestId } = renderWithQuery(<ListeScreen />);
    expect(queryByTestId('liste-grouped-items')).toBeNull();
  });
});

// PO ticket 2026-05-12 — AddItem mutation visibility (R33, no silent
// failures). Each branch of the backend error contract maps to a
// localised toast ; the success path also surfaces a confirmation so
// the user knows the tap was registered. The mocked list-client is
// overridden per-test to drive the mutation outcome.
describe('ListeScreen — AddItem toast surfaces', () => {
  // jest.mock factories above hoist before imports, so we resolve the
  // mocked modules via require to access their typed mock surface
  // (`mockReset`, `mockImplementation`, etc.). Import-style would
  // resolve before the mock is registered.
  /* eslint-disable @typescript-eslint/no-require-imports */
  const listClientMock = require('@/services/list-client').listClient as {
    get: jest.Mock;
    post: jest.Mock;
    patch: jest.Mock;
    delete: jest.Mock;
  };
  const useProductSearchMock = require('@/hooks/use-product-search')
    .useProductSearch as jest.Mock;
  const useActiveListMock = require('@/hooks/use-shopping-lists')
    .useActiveList as jest.Mock;
  /* eslint-enable @typescript-eslint/no-require-imports */

  const HIT = {
    ean: '3017620420001',
    name: 'Lait demi-écrémé 1L',
    brands: 'Lactel',
    categories_tags: null,
    source: 'off',
  };

  beforeEach(() => {
    // Unmount any prior screen so its toast-dismiss setTimeout is
    // cleared (the screen's ``useEffect`` cleanup tears it down).
    // Without this, the 2800 ms dismiss timer of a prior test can
    // fire during the next test and race the new ``findByTestId``.
    cleanup();
    listClientMock.get.mockReset();
    listClientMock.post.mockReset();
    listClientMock.patch.mockReset();
    listClientMock.delete.mockReset();
    // Set ``listId`` synchronously so the AddItem mutation fires the
    // moment the dropdown row is pressed — bypassing the React Query
    // fetch microtask removes a real race the parallel jest workers
    // were hitting (see commit log for the original flake).
    useActiveListMock.mockImplementation(() => ({
      data: {
        id: 'list-1',
        name: '',
        has_default_name: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    }));
    // Make the dropdown surface our hit so a press fires the mutation.
    useProductSearchMock.mockImplementation(() => ({
      data: { items: [HIT] },
      isFetching: false,
    }));
  });

  afterAll(() => {
    // Restore the wide-default the rest of the suite expects so test
    // ordering doesn't bleed mock state.
    useProductSearchMock.mockImplementation(() => ({
      data: { items: [] },
      isFetching: false,
    }));
    useActiveListMock.mockImplementation(() => ({ data: null }));
  });

  async function pickFirstHit(getByTestId: (id: string) => unknown) {
    const input = getByTestId('liste-add-bar-input') as unknown as {
      props: { value: string };
    };
    await act(async () => {
      fireEvent(input as unknown as object, 'focus');
      fireEvent.changeText(input as unknown as object, 'lait');
    });
    await waitFor(() => getByTestId(`liste-add-bar-hit-${HIT.ean}`));
    await act(async () => {
      fireEvent(
        getByTestId(`liste-add-bar-hit-${HIT.ean}`) as unknown as object,
        'pressIn',
      );
    });
    // Mutation fires asynchronously after pressIn. Wait for the
    // POST to be issued so the ``onSuccess``/``onError`` callback
    // chain has a chance to run before the toast assertion.
    await waitFor(() =>
      expect(listClientMock.post).toHaveBeenCalledWith(
        '/lists/list-1/items',
        expect.objectContaining({ product_ean: HIT.ean }),
      ),
    );
  }

  it('shows a success toast after the AddItem mutation resolves', async () => {
    listClientMock.post.mockResolvedValue({
      id: 'item-1',
      product_ean: HIT.ean,
      product_name: HIT.name,
      quantity: 1,
      checked: false,
    });
    const { getByTestId, findByTestId } = renderWithQuery(<ListeScreen />);
    await pickFirstHit(getByTestId);
    const toast = await findByTestId('liste-add-item-toast-text');
    expect(toast.props.children).toContain(HIT.name);
    expect(toast.props.children.toLowerCase()).toContain('ajouté');
  });

  it('shows the « déjà dans ta liste » toast on 409', async () => {
    listClientMock.post.mockRejectedValue(
      new AuthError('item_already_in_list', 'VALIDATION_ERROR', 409),
    );
    const { getByTestId, findByTestId } = renderWithQuery(<ListeScreen />);
    await pickFirstHit(getByTestId);
    const toast = await findByTestId('liste-add-item-toast-text');
    expect(toast.props.children).toContain(HIT.name);
    expect(toast.props.children.toLowerCase()).toContain('déjà');
  });

  it('shows the « liste pleine » toast on 422', async () => {
    listClientMock.post.mockRejectedValue(
      new AuthError('list_full', 'VALIDATION_ERROR', 422),
    );
    const { getByTestId, findByTestId } = renderWithQuery(<ListeScreen />);
    await pickFirstHit(getByTestId);
    const toast = await findByTestId('liste-add-item-toast-text');
    expect(toast.props.children.toLowerCase()).toContain('liste pleine');
  });

  it('shows a generic toast on network / 5xx errors', async () => {
    listClientMock.post.mockRejectedValue(
      new AuthError('upstream_failure', 'SERVER_ERROR', 503),
    );
    const { getByTestId, findByTestId } = renderWithQuery(<ListeScreen />);
    await pickFirstHit(getByTestId);
    const toast = await findByTestId('liste-add-item-toast-text');
    expect(toast.props.children.toLowerCase()).toContain('réessaie');
  });
});
