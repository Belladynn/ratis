import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useAddItem, useToggleItem, useDeleteItem } from '@/hooks/use-list-items';
import { listClient } from '@/services/list-client';

jest.mock('@/services/list-client', () => ({
  listClient: {
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
    get: jest.fn(),
  },
}));

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function makeWrapper(qc: QueryClient) {
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const LIST_ID = '11111111-1111-1111-1111-111111111111';
const ITEM_ID = '22222222-2222-2222-2222-222222222222';
const EAN = '3428270000019';

describe('useAddItem', () => {
  beforeEach(() => {
    (listClient.post as jest.Mock).mockReset();
  });

  it('posts a new item, returns the row, and invalidates caches', async () => {
    const item = {
      id: ITEM_ID,
      product_ean: EAN,
      product_name: 'Lait',
      quantity: 1,
      checked: false,
      checked_at: null,
    };
    (listClient.post as jest.Mock).mockResolvedValue(item);

    const qc = makeClient();
    const spy = jest.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useAddItem(LIST_ID), { wrapper: makeWrapper(qc) });

    let payload: unknown;
    await act(async () => {
      payload = await result.current.mutateAsync({ product_ean: EAN, quantity: 1 });
    });

    expect(listClient.post).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/items`,
      { product_ean: EAN, quantity: 1 },
    );
    expect(payload).toEqual(item);
    expect(spy).toHaveBeenCalledWith({ queryKey: ['list', LIST_ID] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ['route', LIST_ID] });
    // Wave-13 — the freshly added EAN becomes part of tier (c) of
    // ``useDefaultSuggestions`` ; invalidating ensures the next
    // dropdown opening reflects it without waiting for staleTime.
    expect(spy).toHaveBeenCalledWith({ queryKey: ['default-suggestions'] });
  });

  it('defaults quantity to 1 when omitted', async () => {
    (listClient.post as jest.Mock).mockResolvedValue({});
    const qc = makeClient();
    const { result } = renderHook(() => useAddItem(LIST_ID), { wrapper: makeWrapper(qc) });

    await act(async () => {
      await result.current.mutateAsync({ product_ean: EAN });
    });

    expect(listClient.post).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/items`,
      { product_ean: EAN, quantity: 1 },
    );
  });

  it('rejects when listId is null', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => useAddItem(null), { wrapper: makeWrapper(qc) });
    await act(async () => {
      await expect(
        result.current.mutateAsync({ product_ean: EAN }),
      ).rejects.toThrow('no_active_list');
    });
  });

  it('exposes error state on failure', async () => {
    (listClient.post as jest.Mock).mockRejectedValue(new Error('product_not_found'));
    const qc = makeClient();
    const { result } = renderHook(() => useAddItem(LIST_ID), { wrapper: makeWrapper(qc) });
    await act(async () => {
      await expect(
        result.current.mutateAsync({ product_ean: EAN }),
      ).rejects.toThrow('product_not_found');
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useToggleItem', () => {
  beforeEach(() => {
    (listClient.patch as jest.Mock).mockReset();
  });

  it('patches the checked field and invalidates list cache', async () => {
    (listClient.patch as jest.Mock).mockResolvedValue({ id: ITEM_ID });

    const qc = makeClient();
    const spy = jest.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useToggleItem(LIST_ID), { wrapper: makeWrapper(qc) });

    await act(async () => {
      await result.current.mutateAsync({ itemId: ITEM_ID, checked: true });
    });

    expect(listClient.patch).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/items/${ITEM_ID}`,
      { checked: true },
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: ['list', LIST_ID] });
  });

  it('patches the quantity field when provided', async () => {
    (listClient.patch as jest.Mock).mockResolvedValue({ id: ITEM_ID });
    const qc = makeClient();
    const { result } = renderHook(() => useToggleItem(LIST_ID), { wrapper: makeWrapper(qc) });

    await act(async () => {
      await result.current.mutateAsync({ itemId: ITEM_ID, quantity: 3 });
    });

    expect(listClient.patch).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/items/${ITEM_ID}`,
      { quantity: 3 },
    );
  });

  it('rejects when listId is null', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => useToggleItem(null), { wrapper: makeWrapper(qc) });
    await act(async () => {
      await expect(
        result.current.mutateAsync({ itemId: ITEM_ID, checked: true }),
      ).rejects.toThrow('no_active_list');
    });
  });
});

describe('useDeleteItem', () => {
  beforeEach(() => {
    (listClient.delete as jest.Mock).mockReset();
  });

  it('deletes the item and invalidates caches', async () => {
    (listClient.delete as jest.Mock).mockResolvedValue(undefined);
    const qc = makeClient();
    const spy = jest.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useDeleteItem(LIST_ID), { wrapper: makeWrapper(qc) });

    await act(async () => {
      await result.current.mutateAsync({ itemId: ITEM_ID });
    });

    expect(listClient.delete).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/items/${ITEM_ID}`,
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: ['list', LIST_ID] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ['route', LIST_ID] });
  });

  it('rejects when listId is null', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => useDeleteItem(null), { wrapper: makeWrapper(qc) });
    await act(async () => {
      await expect(
        result.current.mutateAsync({ itemId: ITEM_ID }),
      ).rejects.toThrow('no_active_list');
    });
  });
});
