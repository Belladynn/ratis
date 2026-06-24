import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useShoppingLists, useActiveList } from '@/hooks/use-shopping-lists';
import { listClient } from '@/services/list-client';

jest.mock('@/services/list-client', () => ({
  listClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const MOCK_SUMMARY = {
  id: '11111111-1111-1111-1111-111111111111',
  name: null,
  has_default_name: true,
  is_template: false,
  item_count: 5,
  unchecked_count: 3,
  created_at: '2026-04-20T12:00:00+00:00',
  updated_at: '2026-04-20T13:00:00+00:00',
};

describe('useShoppingLists', () => {
  beforeEach(() => {
    (listClient.get as jest.Mock).mockReset();
  });

  it('fetches the user lists', async () => {
    (listClient.get as jest.Mock).mockResolvedValue([MOCK_SUMMARY]);
    const { result } = renderHook(() => useShoppingLists(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listClient.get).toHaveBeenCalledWith('/lists');
    expect(result.current.data).toEqual([MOCK_SUMMARY]);
  });

  it('returns an empty array when user has no lists', async () => {
    (listClient.get as jest.Mock).mockResolvedValue([]);
    const { result } = renderHook(() => useShoppingLists(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });

  it('exposes error state on failure', async () => {
    (listClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useShoppingLists(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useActiveList', () => {
  beforeEach(() => {
    (listClient.get as jest.Mock).mockReset();
  });

  it('returns the first list as the active one', async () => {
    const second = { ...MOCK_SUMMARY, id: '22222222-2222-2222-2222-222222222222' };
    (listClient.get as jest.Mock).mockResolvedValue([MOCK_SUMMARY, second]);
    const { result } = renderHook(() => useActiveList(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.data).toEqual(MOCK_SUMMARY));
  });

  it('returns null when there is no list', async () => {
    (listClient.get as jest.Mock).mockResolvedValue([]);
    const { result } = renderHook(() => useActiveList(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
});
