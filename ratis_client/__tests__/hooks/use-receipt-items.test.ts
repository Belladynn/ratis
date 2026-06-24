import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useReceiptItems } from '@/hooks/use-receipt-items';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const RECEIPT_ID = '11111111-1111-1111-1111-111111111111';

const RECEIPT_ITEM_ACCEPTED = {
  scan_id: 'aaaaaaaa-1111-1111-1111-111111111111',
  scanned_name: 'LAIT DE DE-ECR',
  product_name: 'Lait demi-écrémé 1L',
  product_ean: '3428270000019',
  quantity: 1,
  price_cents: 129,
  status: 'accepted',
  match_method: 'barcode_ean',
};

const RECEIPT_ITEM_UNMATCHED = {
  scan_id: 'aaaaaaaa-2222-2222-2222-222222222222',
  scanned_name: null,
  product_name: null,
  product_ean: null,
  quantity: 1,
  price_cents: 150,
  status: 'unmatched',
  match_method: null,
};

const RECEIPT_RESPONSE = {
  status: 'done',
  matched: 1,
  unmatched: 1,
  total_amount: 279,
  store_status: 'confirmed',
  pending_items_count: 0,
  items: [RECEIPT_ITEM_ACCEPTED, RECEIPT_ITEM_UNMATCHED],
};

describe('useReceiptItems', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
  });

  it('is disabled until the caller flips enabled=true', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(RECEIPT_RESPONSE);
    const { result } = renderHook(
      () => useReceiptItems(RECEIPT_ID, { enabled: false }),
      { wrapper: makeWrapper() },
    );
    // Give React a microtask to settle — nothing should have been fetched.
    await new Promise((r) => setTimeout(r, 10));
    expect(productClient.get).not.toHaveBeenCalled();
    expect(result.current.isFetching).toBe(false);
  });

  it('fetches the receipt with items[] when enabled', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(RECEIPT_RESPONSE);
    const { result } = renderHook(
      () => useReceiptItems(RECEIPT_ID, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith(`/scan/receipt/${RECEIPT_ID}`);
    expect(result.current.data?.items).toHaveLength(2);
    expect(result.current.data?.items[0].match_method).toBe('barcode_ean');
    expect(result.current.data?.items[1].status).toBe('unmatched');
  });

  it('skips fetch when receiptId is null even if enabled=true', async () => {
    (productClient.get as jest.Mock).mockResolvedValue(RECEIPT_RESPONSE);
    renderHook(
      () => useReceiptItems(null, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    await new Promise((r) => setTimeout(r, 10));
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('exposes error state when the request fails', async () => {
    (productClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(
      () => useReceiptItems(RECEIPT_ID, { enabled: true }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
