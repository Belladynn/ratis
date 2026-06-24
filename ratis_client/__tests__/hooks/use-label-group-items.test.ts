import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useLabelGroupItems } from '@/hooks/use-label-group-items';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const STORE_ID = '22222222-2222-2222-2222-222222222222';
const DATE = '2026-04-24';

const LABEL_ITEM = {
  scan_id: 'bbbbbbbb-1111-1111-1111-111111111111',
  product_name: 'Yaourt Danone nature',
  product_ean: '3033490004057',
  price_cents: 115,
  match_method: 'barcode_ean',
  scanned_at: '2026-04-24T09:30:00+00:00',
};

describe('useLabelGroupItems', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
  });

  it('does not fetch until enabled=true', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [LABEL_ITEM] });
    renderHook(
      () => useLabelGroupItems(STORE_ID, DATE, { enabled: false }),
      { wrapper: makeWrapper() },
    );
    await new Promise((r) => setTimeout(r, 10));
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('fetches accepted items for the group when enabled', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [LABEL_ITEM] });
    const { result } = renderHook(
      () => useLabelGroupItems(STORE_ID, DATE, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(
      `/scan/label-group?store_id=${STORE_ID}&date=${DATE}`,
    );
    expect(result.current.data?.items).toHaveLength(1);
    expect(result.current.data?.items[0].product_ean).toBe('3033490004057');
  });

  it('stays disabled when storeId or date is missing', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    renderHook(
      () => useLabelGroupItems(null, DATE, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    renderHook(
      () => useLabelGroupItems(STORE_ID, null, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    await new Promise((r) => setTimeout(r, 10));
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('surfaces errors via isError', async () => {
    (productClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(
      () => useLabelGroupItems(STORE_ID, DATE, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
