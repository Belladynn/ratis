import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useScanCheck } from '@/hooks/use-scan-check';
import { listClient } from '@/services/list-client';

jest.mock('@/services/list-client', () => ({
  listClient: { post: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const LIST_ID = '11111111-1111-1111-1111-111111111111';
const EAN = '3428270000019';

describe('useScanCheck', () => {
  beforeEach(() => {
    (listClient.post as jest.Mock).mockReset();
  });

  it('returns status "checked" and the updated item', async () => {
    const mockItem = {
      id: 'item-1',
      product_ean: EAN,
      name: 'Lait demi-écrémé 1L',
      quantity: 1,
      checked: true,
    };
    (listClient.post as jest.Mock).mockResolvedValue({
      status: 'checked',
      item: mockItem,
    });

    const { result } = renderHook(() => useScanCheck(LIST_ID), { wrapper: makeWrapper() });
    let payload: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      payload = await result.current.mutateAsync({ productEan: EAN });
    });

    expect(listClient.post).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/scan-check`,
      { product_ean: EAN },
    );
    expect(payload?.status).toBe('checked');
    expect(payload?.item).toEqual(mockItem);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('returns status "already_checked" when item was already ticked', async () => {
    const mockItem = {
      id: 'item-1',
      product_ean: EAN,
      name: 'Lait demi-écrémé 1L',
      quantity: 1,
      checked: true,
    };
    (listClient.post as jest.Mock).mockResolvedValue({
      status: 'already_checked',
      item: mockItem,
    });

    const { result } = renderHook(() => useScanCheck(LIST_ID), { wrapper: makeWrapper() });
    let payload: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      payload = await result.current.mutateAsync({ productEan: EAN });
    });

    expect(payload?.status).toBe('already_checked');
  });

  it('returns status "not_in_list" with product when EAN is known but absent', async () => {
    (listClient.post as jest.Mock).mockResolvedValue({
      status: 'not_in_list',
      product: { ean: EAN, name: 'Lait demi-écrémé 1L' },
    });

    const { result } = renderHook(() => useScanCheck(LIST_ID), { wrapper: makeWrapper() });
    let payload: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      payload = await result.current.mutateAsync({ productEan: EAN });
    });

    expect(payload?.status).toBe('not_in_list');
    expect(payload?.product).toEqual({ ean: EAN, name: 'Lait demi-écrémé 1L' });
  });

  it('exposes error state when request fails', async () => {
    (listClient.post as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useScanCheck(LIST_ID), { wrapper: makeWrapper() });

    await act(async () => {
      await expect(result.current.mutateAsync({ productEan: EAN })).rejects.toThrow('boom');
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
