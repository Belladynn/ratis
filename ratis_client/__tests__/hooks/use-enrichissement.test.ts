import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useEnrichissement } from '@/hooks/use-enrichissement';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useEnrichissement', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('hits the correct endpoint /product/incomplete?limit=1', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useEnrichissement(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith('/product/incomplete?limit=1');
  });

  it('returns the first task from items[] on success', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      items: [
        {
          product_ean: '123', product_name: 'P1',
          missing_field: 'brands', cab_reward: 5,
        },
      ],
    });
    const { result } = renderHook(() => useEnrichissement(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.product_ean).toBe('123');
    expect(result.current.data?.missing_field).toBe('brands');
  });

  it('returns null when items[] is empty', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useEnrichissement(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('returns null (not error) when API fails — card hides silently', async () => {
    (productClient.get as jest.Mock).mockRejectedValue(new Error('no-task'));
    const { result } = renderHook(() => useEnrichissement(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
});
