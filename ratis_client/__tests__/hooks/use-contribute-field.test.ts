import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useContributeField } from '@/hooks/use-contribute-field';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { post: jest.fn() },
}));

describe('useContributeField', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  function setup() {
    const qc = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);
    return { qc, invalidateSpy, wrapper };
  }

  it('POSTs to /product/{ean}/contribute with body', async () => {
    const { wrapper } = setup();
    (productClient.post as jest.Mock).mockResolvedValue({ status: 'applied' });
    const { result } = renderHook(() => useContributeField(), { wrapper });
    await result.current.mutateAsync({
      ean: '9990000000001',
      field: 'brands',
      value: 'Lactel',
    });
    expect(productClient.post).toHaveBeenCalledWith(
      '/product/9990000000001/contribute',
      { field: 'brands', value: 'Lactel' },
    );
  });

  it('invalidates 3 queryKeys on success', async () => {
    const { invalidateSpy, wrapper } = setup();
    (productClient.post as jest.Mock).mockResolvedValue({ status: 'applied' });
    const { result } = renderHook(() => useContributeField(), { wrapper });
    await result.current.mutateAsync({
      ean: '9990000000001',
      field: 'brands',
      value: 'Lactel',
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const keys = invalidateSpy.mock.calls.map(([opts]) => opts?.queryKey?.[0]);
    expect(keys).toEqual(
      expect.arrayContaining([
        'enrichissement',
        'incomplete-products',
        'cab-balance',
      ]),
    );
  });
});
