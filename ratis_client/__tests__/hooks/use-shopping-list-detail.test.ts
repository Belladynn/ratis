import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useShoppingListDetail } from '@/hooks/use-shopping-list-detail';
import { listClient } from '@/services/list-client';

jest.mock('@/services/list-client', () => ({
  listClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const LIST_ID = '11111111-1111-1111-1111-111111111111';

const MOCK_DETAIL = {
  id: LIST_ID,
  name: null,
  has_default_name: true,
  is_template: false,
  items: [
    {
      id: 'item-1',
      product_ean: '3428270000019',
      product_name: 'Lait demi-écrémé 1L',
      quantity: 1,
      checked: false,
      checked_at: null,
    },
  ],
  created_at: '2026-04-20T12:00:00+00:00',
  updated_at: '2026-04-20T13:00:00+00:00',
};

describe('useShoppingListDetail', () => {
  beforeEach(() => {
    (listClient.get as jest.Mock).mockReset();
  });

  it('fetches the list detail by id', async () => {
    (listClient.get as jest.Mock).mockResolvedValue(MOCK_DETAIL);
    const { result } = renderHook(() => useShoppingListDetail(LIST_ID), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listClient.get).toHaveBeenCalledWith(`/lists/${LIST_ID}`);
    expect(result.current.data).toEqual(MOCK_DETAIL);
  });

  it('does not fire when listId is null', () => {
    (listClient.get as jest.Mock).mockResolvedValue(MOCK_DETAIL);
    const { result } = renderHook(() => useShoppingListDetail(null), { wrapper: makeWrapper() });
    expect(listClient.get).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
  });

  it('exposes error state on failure', async () => {
    (listClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useShoppingListDetail(LIST_ID), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
