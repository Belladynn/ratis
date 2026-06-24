// __tests__/hooks/use-default-suggestions.test.ts
//
// useDefaultSuggestions — wave-13 (PO ticket 2026-05-14 follow-up) hook
// wrapping GET /api/v1/product/suggestions/default for the empty-state of
// the Liste/Produit search field. See
// ``docs/superpowers/specs/2026-05-14-default-search-3tier-design.md``.

import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useDefaultSuggestions } from '@/hooks/use-default-suggestions';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useDefaultSuggestions', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
  });

  it('calls the default endpoint with the given limit', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(
      () => useDefaultSuggestions({ limit: 3 }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      '/product/suggestions/default?limit=3',
    );
  });

  it('defaults to limit=5 when not provided', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useDefaultSuggestions(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      '/product/suggestions/default?limit=5',
    );
  });

  it('returns items from the response', async () => {
    const items = [
      {
        ean: '111',
        name: 'Lait',
        brands: 'Lactel',
        quantity: '1L',
        categories_tags: null,
        labels_tags: null,
        origins_tags: null,
        source: 'off',
      },
    ];
    (productClient.get as jest.Mock).mockResolvedValue({ items });
    const { result } = renderHook(
      () => useDefaultSuggestions({ limit: 5 }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toEqual(items);
  });
});
