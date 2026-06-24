import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useScanHistory } from '@/hooks/use-scan-history';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const RECEIPT_ENTRY = {
  type: 'receipt',
  receipt_id: '11111111-1111-1111-1111-111111111111',
  scanned_at: '2026-04-24T10:00:00+00:00',
  store_name: 'Carrefour Ménilmontant',
  store_status: 'confirmed',
  total_amount_cents: 4735,
  matched_count: 10,
  unmatched_count: 2,
  pending_count: 0,
};

const LABEL_GROUP_ENTRY = {
  type: 'label_group',
  group_key: '22222222-2222-2222-2222-222222222222|2026-04-24',
  store_id: '22222222-2222-2222-2222-222222222222',
  date: '2026-04-24',
  store_name: 'Monoprix République',
  latest_scanned_at: '2026-04-24T09:30:00+00:00',
  accepted_count: 8,
};

describe('useScanHistory', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
  });

  it('fetches the first page of unified history entries with default limit 20', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      entries: [RECEIPT_ENTRY, LABEL_GROUP_ENTRY],
      next_cursor: null,
    });
    const { result } = renderHook(() => useScanHistory(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // No cursor on the first request
    expect(productClient.get).toHaveBeenCalledWith('/scan/history?limit=20');

    // Pages exposed by useInfiniteQuery — flatten entries for consumers
    const pages = result.current.data?.pages ?? [];
    expect(pages.length).toBe(1);
    expect(pages[0].entries).toHaveLength(2);
    expect(pages[0].entries[0].type).toBe('receipt');
    expect(pages[0].entries[1].type).toBe('label_group');
    expect(result.current.hasNextPage).toBe(false);
  });

  it('exposes a next page when the backend returns a cursor', async () => {
    (productClient.get as jest.Mock)
      .mockResolvedValueOnce({
        entries: [RECEIPT_ENTRY],
        next_cursor: 'opaque-cursor-1',
      })
      .mockResolvedValueOnce({
        entries: [LABEL_GROUP_ENTRY],
        next_cursor: null,
      });
    const { result } = renderHook(() => useScanHistory(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));

    // 2nd call carries the opaque cursor verbatim (URL-encoded)
    expect(productClient.get).toHaveBeenNthCalledWith(
      2,
      '/scan/history?limit=20&cursor=opaque-cursor-1',
    );
    expect(result.current.hasNextPage).toBe(false);
  });

  it('uses the provided limit', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ entries: [], next_cursor: null });
    const { result } = renderHook(() => useScanHistory(5), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith('/scan/history?limit=5');
  });

  it('returns empty entries for a new user', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ entries: [], next_cursor: null });
    const { result } = renderHook(() => useScanHistory(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pages[0].entries).toEqual([]);
    expect(result.current.hasNextPage).toBe(false);
  });

  it('encodes cursor values containing URL-reserved characters', async () => {
    const cursorWithSlashes = 'eyJhIjogIjIwMjYifQ==';
    (productClient.get as jest.Mock)
      .mockResolvedValueOnce({ entries: [RECEIPT_ENTRY], next_cursor: cursorWithSlashes })
      .mockResolvedValueOnce({ entries: [], next_cursor: null });
    const { result } = renderHook(() => useScanHistory(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    await act(async () => {
      await result.current.fetchNextPage();
    });

    expect(productClient.get).toHaveBeenNthCalledWith(
      2,
      `/scan/history?limit=20&cursor=${encodeURIComponent(cursorWithSlashes)}`,
    );
  });

  it('exposes error state on failure', async () => {
    (productClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useScanHistory(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('auto-refetches the first page when refetchInterval is set (pending poll)', async () => {
    // Bug fix 2026-05-01 — the scan tab passes refetchInterval while a
    // pending receipt is in-flight so the row flips from "Traitement en cours"
    // to its final state without manual refresh. This test uses Jest fake
    // timers to advance past the interval and assert a second network call.
    jest.useFakeTimers();
    try {
      (productClient.get as jest.Mock).mockResolvedValue({
        entries: [RECEIPT_ENTRY],
        next_cursor: null,
      });
      const { result } = renderHook(
        () => useScanHistory(20, { refetchInterval: 1_000 }),
        { wrapper: makeWrapper() },
      );
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(productClient.get).toHaveBeenCalledTimes(1);

      // Advance past the refetch interval — React Query schedules another
      // fetch on the active query.
      await act(async () => {
        jest.advanceTimersByTime(1_500);
      });
      await waitFor(() =>
        expect(productClient.get).toHaveBeenCalledTimes(2),
      );
    } finally {
      jest.useRealTimers();
    }
  });

  it('does NOT auto-refetch when refetchInterval is omitted (default off)', async () => {
    jest.useFakeTimers();
    try {
      (productClient.get as jest.Mock).mockResolvedValue({
        entries: [RECEIPT_ENTRY],
        next_cursor: null,
      });
      const { result } = renderHook(() => useScanHistory(), { wrapper: makeWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(productClient.get).toHaveBeenCalledTimes(1);

      await act(async () => {
        jest.advanceTimersByTime(60_000);
      });
      // Still one call — no polling.
      expect(productClient.get).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
    }
  });
});
