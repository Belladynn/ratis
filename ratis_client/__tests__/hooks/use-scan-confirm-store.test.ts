import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useScanConfirmStore } from '@/hooks/use-scan-confirm-store';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { post: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
  return { wrapper, qc };
}

const RECEIPT_ID = '11111111-1111-1111-1111-111111111111';

describe('useScanConfirmStore', () => {
  beforeEach(() => {
    (productClient.post as jest.Mock).mockReset();
  });

  it('POSTs to /scan/receipt/{id}/confirm-store with no body', async () => {
    (productClient.post as jest.Mock).mockResolvedValue({
      store_status: 'pending',
      store_id: 'store-uuid',
      validation_status: 'pending',
      message: 'store_pending_validation',
    });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useScanConfirmStore(RECEIPT_ID), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(productClient.post).toHaveBeenCalledWith(
      `/scan/receipt/${RECEIPT_ID}/confirm-store`,
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('invalidates scan-history, receipt-items and receipt caches on success', async () => {
    (productClient.post as jest.Mock).mockResolvedValue({
      store_status: 'pending',
      store_id: 'store-uuid',
      validation_status: 'pending',
      message: 'store_pending_validation',
    });
    const { wrapper, qc } = makeWrapper();
    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useScanConfirmStore(RECEIPT_ID), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['scan-history'] });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['receipt-items', RECEIPT_ID],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['receipt', RECEIPT_ID],
    });
  });

  it('surfaces backend errors (e.g. 422 insufficient_ocr_data) to the caller', async () => {
    (productClient.post as jest.Mock).mockRejectedValue(new Error('insufficient_ocr_data'));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useScanConfirmStore(RECEIPT_ID), { wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toThrow('insufficient_ocr_data');
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
