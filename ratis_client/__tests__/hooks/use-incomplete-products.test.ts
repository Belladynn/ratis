import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useIncompleteProducts } from '@/hooks/use-incomplete-products';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function wrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useIncompleteProducts', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('calls /product/incomplete with the given limit', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(
      () => useIncompleteProducts({ limit: 5 }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith('/product/incomplete?limit=5');
  });

  it('defaults to limit=10 when not provided', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useIncompleteProducts(), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith('/product/incomplete?limit=10');
  });

  it('returns items from response', async () => {
    const items = [
      {
        product_ean: '9990000000001',
        product_name: 'Lait',
        missing_field: 'brands',
        cab_reward: 5,
      },
    ];
    (productClient.get as jest.Mock).mockResolvedValue({ items });
    const { result } = renderHook(() => useIncompleteProducts(), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toEqual(items);
  });
});
