import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useProductByEan } from '@/hooks/use-product-by-ean';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const MOCK_RESPONSE = {
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
  nearby_prices: [
    {
      store_id: '11111111-1111-1111-1111-111111111111',
      store_name: 'Leclerc Parmentier',
      price: 1.19,
      distance_km: 1.2,
    },
  ],
};

describe('useProductByEan', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
  });

  it('fetches product without coordinates when ean is provided', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(MOCK_RESPONSE);
    const { result } = renderHook(
      () => useProductByEan('3428270000019'),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith('/product/3428270000019');
    expect(result.current.data).toEqual(MOCK_RESPONSE);
  });

  it('includes user_lat and user_lng in the query string when provided', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(MOCK_RESPONSE);
    const { result } = renderHook(
      () => useProductByEan('3428270000019', { lat: 48.86, lng: 2.34 }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      '/product/3428270000019?user_lat=48.86&user_lng=2.34',
    );
  });

  it('includes store_id when provided', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(MOCK_RESPONSE);
    const { result } = renderHook(
      () =>
        useProductByEan('3428270000019', {
          storeId: '22222222-2222-2222-2222-222222222222',
        }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      '/product/3428270000019?store_id=22222222-2222-2222-2222-222222222222',
    );
  });

  it('does not fetch when ean is null', () => {
    const { result } = renderHook(
      () => useProductByEan(null),
      { wrapper: makeWrapper() },
    );
    expect(productClient.get).not.toHaveBeenCalled();
    expect(result.current.isPending).toBe(true);
  });

  it('exposes error state on 404', async () => {
    (productClient.get as jest.Mock).mockRejectedValue(new Error('not_found'));
    const { result } = renderHook(
      () => useProductByEan('1111111111111'),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
