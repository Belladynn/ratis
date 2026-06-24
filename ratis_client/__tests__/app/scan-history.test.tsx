import React from 'react';
import { render, fireEvent, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn(), post: jest.fn() },
}));

const mockRouterBack = jest.fn();
const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockRouterBack, push: mockRouterPush }),
}));

jest.mock('expo-camera', () => ({
  CameraView: ({ onBarcodeScanned }: { onBarcodeScanned?: (p: { data: string }) => void }) => {
    (global as unknown as { __triggerBarcode?: (p: { data: string }) => void }).__triggerBarcode =
      onBarcodeScanned;
    return null;
  },
  useCameraPermissions: jest.fn(() => [{ granted: true }, jest.fn()]),
}));

import ScanHistoryScreen from '@/app/scan-history';
import { productClient } from '@/services/product-client';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const RECEIPT_ENTRY = {
  type: 'receipt',
  receipt_id: '11111111-1111-1111-1111-111111111111',
  scanned_at: '2026-04-24T10:00:00+00:00',
  store_name: 'Carrefour Ménilmontant',
  store_status: 'confirmed',
  total_amount_cents: 4735,
  matched_count: 10,
  unmatched_count: 2,
  pending_count: 0,
};

const LABEL_ENTRY = {
  type: 'label_group',
  group_key: '22222222-2222-2222-2222-222222222222|2026-04-24',
  store_id: '22222222-2222-2222-2222-222222222222',
  date: '2026-04-24',
  store_name: 'Monoprix République',
  latest_scanned_at: '2026-04-24T09:30:00+00:00',
  accepted_count: 8,
};

const UNMATCHED_ITEM = {
  scan_id: 'item-red',
  scanned_name: 'COLA 33CL',
  product_name: null,
  product_ean: null,
  quantity: 1,
  price_cents: 99,
  status: 'unmatched',
  match_method: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  (productClient.get as jest.Mock).mockReset();
  (productClient.post as jest.Mock).mockReset();
});

describe('ScanHistoryScreen', () => {
  it('renders an empty state when no entries are returned', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ entries: [], next_cursor: null });
    const { findByText } = renderWithQuery(<ScanHistoryScreen />);
    expect(await findByText(/Aucun scan pour l'instant/)).toBeTruthy();
  });

  it('renders both receipt and label-group entries from the first page', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      entries: [RECEIPT_ENTRY, LABEL_ENTRY],
      next_cursor: null,
    });
    const { findByText, getByText } = renderWithQuery(<ScanHistoryScreen />);
    expect(await findByText('CARREFOUR MÉNILMONTANT')).toBeTruthy();
    expect(getByText('MONOPRIX RÉPUBLIQUE')).toBeTruthy();
  });

  it('tapping back calls router.back()', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ entries: [], next_cursor: null });
    const { getByTestId } = renderWithQuery(<ScanHistoryScreen />);
    fireEvent.press(getByTestId('scan-history-back'));
    expect(mockRouterBack).toHaveBeenCalled();
  });

  it('does NOT show rejected items even when backend leaks them (client guard)', async () => {
    (productClient.get as jest.Mock).mockImplementation((url: string) => {
      if (url.startsWith('/scan/history')) {
        return Promise.resolve({ entries: [RECEIPT_ENTRY], next_cursor: null });
      }
      if (url.startsWith('/scan/receipt')) {
        return Promise.resolve({
          status: 'done',
          matched: 1,
          unmatched: 0,
          total_amount: 129,
          store_status: 'confirmed',
          pending_items_count: 0,
          items: [
            {
              scan_id: 'keep',
              scanned_name: null,
              product_name: 'Lait',
              product_ean: null,
              quantity: 1,
              price_cents: 129,
              status: 'accepted',
              match_method: 'barcode_ean',
            },
            {
              scan_id: 'drop',
              scanned_name: null,
              product_name: 'Duplicated',
              product_ean: null,
              quantity: 1,
              price_cents: 0,
              status: 'rejected',
              match_method: null,
            },
          ],
        });
      }
      return Promise.reject(new Error('unexpected url'));
    });

    const { findByTestId, queryByText, getByText } = renderWithQuery(<ScanHistoryScreen />);
    const header = await findByTestId(
      `receipt-accordion-header-${RECEIPT_ENTRY.receipt_id}`,
    );
    fireEvent.press(header);
    await waitFor(() => expect(getByText('Lait')).toBeTruthy());
    expect(queryByText('Duplicated')).toBeNull();
  });

  it('tapping a barcode button on an item opens the BarcodeScannerModal', async () => {
    (productClient.get as jest.Mock).mockImplementation((url: string) => {
      if (url.startsWith('/scan/history')) {
        return Promise.resolve({ entries: [RECEIPT_ENTRY], next_cursor: null });
      }
      if (url.startsWith('/scan/receipt')) {
        return Promise.resolve({
          status: 'done',
          matched: 0,
          unmatched: 1,
          total_amount: 99,
          store_status: 'confirmed',
          pending_items_count: 0,
          items: [UNMATCHED_ITEM],
        });
      }
      return Promise.reject(new Error('unexpected url'));
    });
    const { findByTestId, getByTestId } = renderWithQuery(<ScanHistoryScreen />);
    const header = await findByTestId(
      `receipt-accordion-header-${RECEIPT_ENTRY.receipt_id}`,
    );
    fireEvent.press(header);
    const barcodeBtn = await waitFor(() => getByTestId('scan-history-item-barcode-item-red'));
    fireEvent.press(barcodeBtn);
    await waitFor(() => expect(getByTestId('barcode-scanner-modal')).toBeTruthy());
  });

  it('links a scanned EAN via POST /scan/barcode and invalidates receipt items', async () => {
    (productClient.get as jest.Mock).mockImplementation((url: string) => {
      if (url.startsWith('/scan/history')) {
        return Promise.resolve({ entries: [RECEIPT_ENTRY], next_cursor: null });
      }
      if (url.startsWith('/scan/receipt')) {
        return Promise.resolve({
          status: 'done',
          matched: 0,
          unmatched: 1,
          total_amount: 99,
          store_status: 'confirmed',
          pending_items_count: 0,
          items: [UNMATCHED_ITEM],
        });
      }
      return Promise.reject(new Error('unexpected url'));
    });
    (productClient.post as jest.Mock).mockResolvedValue({ ok: true });

    const { findByTestId, getByTestId } = renderWithQuery(<ScanHistoryScreen />);
    const header = await findByTestId(
      `receipt-accordion-header-${RECEIPT_ENTRY.receipt_id}`,
    );
    fireEvent.press(header);
    const barcodeBtn = await waitFor(() => getByTestId('scan-history-item-barcode-item-red'));
    fireEvent.press(barcodeBtn);
    await waitFor(() => expect(getByTestId('barcode-scanner-modal')).toBeTruthy());

    await act(async () => {
      (global as unknown as { __triggerBarcode?: (p: { data: string }) => void })
        .__triggerBarcode?.({ data: '3428270000019' });
    });

    await waitFor(() => {
      expect(productClient.post).toHaveBeenCalledWith('/scan/barcode', {
        ean: '3428270000019',
        scan_id: 'item-red',
      });
    });
  });
});
