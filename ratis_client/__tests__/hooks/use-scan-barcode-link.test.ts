import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useScanBarcodeLink } from '@/hooks/use-scan-barcode-link';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { post: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
  return { wrapper, qc };
}

const SCAN_ID = 'aaaaaaaa-1111-1111-1111-111111111111';
const RECEIPT_ID = '11111111-1111-1111-1111-111111111111';
const EAN = '3428270000019';

describe('useScanBarcodeLink', () => {
  beforeEach(() => {
    (productClient.post as jest.Mock).mockReset();
  });

  it('POSTs to /scan/barcode with ean + scan_id', async () => {
    (productClient.post as jest.Mock).mockResolvedValue({ ok: true });
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useScanBarcodeLink(RECEIPT_ID), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ ean: EAN, scan_id: SCAN_ID });
    });
    expect(productClient.post).toHaveBeenCalledWith('/scan/barcode', {
      ean: EAN,
      scan_id: SCAN_ID,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('invalidates the receipt-items cache on success', async () => {
    (productClient.post as jest.Mock).mockResolvedValue({ ok: true });
    const { wrapper, qc } = makeWrapper();
    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useScanBarcodeLink(RECEIPT_ID), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ ean: EAN, scan_id: SCAN_ID });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['receipt-items', RECEIPT_ID],
    });
  });

  it('surfaces backend errors (e.g. 409 product_mismatch) to the caller', async () => {
    (productClient.post as jest.Mock).mockRejectedValue(new Error('product_mismatch'));
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useScanBarcodeLink(RECEIPT_ID), { wrapper });

    await act(async () => {
      await expect(
        result.current.mutateAsync({ ean: EAN, scan_id: SCAN_ID }),
      ).rejects.toThrow('product_mismatch');
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
