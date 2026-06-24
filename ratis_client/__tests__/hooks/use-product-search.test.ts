// __tests__/hooks/use-product-search.test.ts
//
// useProductSearch (wave 4 Bug 3) — drives the Liste AddBar autocomplete
// and the new Produit tab search. Wraps React Query on top of the
// product-client with a 300 ms debounce on the input value.

import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useProductSearch } from '@/hooks/use-product-search';
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

// Wave 9 — mock fixtures track the enriched ProductSearchHit shape
// (quantity / labels_tags / origins_tags) added to the backend response.
const HITS = {
  items: [
    {
      ean: '3017620420001',
      name: 'Lait demi-écrémé 1L',
      brands: 'Lactel',
      quantity: '1 L',
      categories_tags: null,
      labels_tags: null,
      origins_tags: ['en:france'],
      source: 'off',
    },
  ],
};

describe('useProductSearch', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('does not fire while the debounce window is open', () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    renderHook(() => useProductSearch('lait'), { wrapper: makeWrapper() });
    act(() => {
      jest.advanceTimersByTime(100);
    });
    expect(productClient.get).not.toHaveBeenCalled();
  });

  // Wave 12 (PO ticket 2026-05-14) — single-char queries are now
  // queryable. The backend lifted its ``min_length=2`` validator so the
  // hook follows. The 300 ms debounce still applies to typed input.
  it('fires for a single-char query once the debounce elapses', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    const { result } = renderHook(() => useProductSearch('l'), {
      wrapper: makeWrapper(),
    });
    act(() => {
      jest.advanceTimersByTime(310);
    });
    jest.useRealTimers();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      expect.stringContaining('/product/search?q=l'),
    );
  });

  // Wave 13 (PO ticket 2026-05-14 follow-up) — empty/whitespace
  // queries are now always inert. Empty-state suggestions are served
  // by the dedicated ``useDefaultSuggestions`` hook against
  // ``GET /api/v1/product/suggestions/default``. The wave-12
  // ``defaultMode`` / ``defaultLimit`` options were removed ; the
  // backend ``q=""`` alphabetic fallback stays alive for back-compat
  // (deferred Phase-3 cleanup PR).
  it('does NOT fire when the query is empty', () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    renderHook(() => useProductSearch(''), { wrapper: makeWrapper() });
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('does NOT fire when the query is whitespace only', () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    renderHook(() => useProductSearch('   '), { wrapper: makeWrapper() });
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('fires once the debounce window elapses', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    const { result } = renderHook(() => useProductSearch('lait'), {
      wrapper: makeWrapper(),
    });
    act(() => {
      jest.advanceTimersByTime(310);
    });
    jest.useRealTimers();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      expect.stringContaining('/product/search?q=lait'),
    );
    expect(result.current.data).toEqual(HITS);
  });

  it('honours the enabled flag', () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    renderHook(() => useProductSearch('lait', { enabled: false }), {
      wrapper: makeWrapper(),
    });
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('round-trips the enriched fields (quantity / labels_tags / origins_tags) untouched', async () => {
    // Wave 9 — type-level assertion that the hook does NOT strip the
    // new fields. The HITS fixture above carries them ; we check the
    // shape lands intact on the consumer side.
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    const { result } = renderHook(() => useProductSearch('lait'), {
      wrapper: makeWrapper(),
    });
    act(() => {
      jest.advanceTimersByTime(310);
    });
    jest.useRealTimers();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const item = result.current.data!.items[0];
    expect(item.quantity).toBe('1 L');
    expect(item.brands).toBe('Lactel');
    expect(item.origins_tags).toEqual(['en:france']);
    expect(item.labels_tags).toBeNull();
  });

  it('encodes special characters in the query', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(HITS);
    renderHook(() => useProductSearch('lait & co'), {
      wrapper: makeWrapper(),
    });
    act(() => {
      jest.advanceTimersByTime(310);
    });
    jest.useRealTimers();
    await waitFor(() =>
      expect(productClient.get).toHaveBeenCalledWith(
        expect.stringContaining('q=lait%20%26%20co'),
      ),
    );
  });
});
