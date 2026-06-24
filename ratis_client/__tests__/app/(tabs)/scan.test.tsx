import React from 'react';
import { act, render, fireEvent, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockTakePictureAsync = jest.fn().mockResolvedValue({ uri: 'file:///mock.jpg' });

jest.mock('expo-camera', () => {
  const React = require('react');
  const CameraView = React.forwardRef((props: any, ref: any) => {
    React.useImperativeHandle(ref, () => ({ takePictureAsync: mockTakePictureAsync }));
    return null;
  });
  return {
    CameraView,
    useCameraPermissions: () => [{ granted: true }, jest.fn()],
  };
});

jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  getCurrentPositionAsync: jest.fn().mockResolvedValue({
    coords: { latitude: 48.8566, longitude: 2.3522 },
  }),
  Accuracy: { Balanced: 3 },
}));

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
// Mirrors the new unified shape of useScanHistory — pages[] + entries[].
// A receipt entry projects to { name: store_name, price: total_amount_cents/100 }
// in the compact overlay. The mock exposes an `__setNextEntries` helper so a
// test can swap the entries the hook returns (e.g. simulate a `pending`
// receipt). Default: a single confirmed receipt at 1.25€.
const __mockEntriesRef: { current: unknown[] } = {
  current: [
    {
      type: 'receipt',
      receipt_id: '11111111-1111-1111-1111-111111111111',
      scanned_at: '2026-04-20T12:00:00+00:00',
      store_name: 'Carrefour',
      store_status: 'confirmed',
      total_amount_cents: 125,
      matched_count: 1,
      unmatched_count: 0,
      pending_count: 0,
    },
  ],
};
jest.mock('@/hooks/use-scan-history', () => ({
  SCAN_HISTORY_QUERY_KEY: ['scan-history'] as const,
  useScanHistory: jest.fn(() => ({
    data: {
      pages: [{ entries: __mockEntriesRef.current, next_cursor: null }],
      pageParams: [null],
    },
    isLoading: false,
    isError: false,
    hasNextPage: false,
    fetchNextPage: jest.fn(),
    isFetchingNextPage: false,
    refetch: jest.fn(),
  })),
}));

// Mirrors the local AsyncStorage-backed hook — feeds `localOrphans` in the
// scan tab so tests can simulate stale `error` items left over from past
// failed uploads and assert they don't dominate the abridged preview.
const __mockPendingRef: { current: unknown[] } = { current: [] };
jest.mock('@/hooks/use-pending-scans', () => ({
  usePendingScans: jest.fn(() => ({
    data: __mockPendingRef.current,
    isLoading: false,
    isError: false,
    refetch: jest.fn(),
  })),
}));

const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockRouterPush }),
}));

const mockEnqueueReceipt = jest.fn().mockResolvedValue('id-r');
const mockEnqueueLabel = jest.fn().mockResolvedValue('id-l');
const mockProcessQueue = jest.fn().mockResolvedValue(undefined);

jest.mock('@/services/scan-queue', () => ({
  enqueueReceipt: (...args: unknown[]) => mockEnqueueReceipt(...args),
  enqueueLabel: (...args: unknown[]) => mockEnqueueLabel(...args),
  processQueue: (...args: unknown[]) => mockProcessQueue(...args),
}));

import ScanScreen from '@/app/(tabs)/scan';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('ScanScreen (theme v2)', () => {
  beforeEach(() => {
    mockTakePictureAsync.mockClear();
    mockEnqueueReceipt.mockClear();
    mockEnqueueLabel.mockClear();
    mockProcessQueue.mockClear();
  });

  it('renders without crash', () => {
    const { toJSON } = renderWithQuery(<ScanScreen />);
    expect(toJSON()).not.toBeNull();
  });

  it('renders history overlay + mode switch + capture button (no top send btn in receipt mode — AF-09)', () => {
    const { getByText, getByTestId, queryByTestId } = renderWithQuery(<ScanScreen />);
    expect(getByText('Historique produits scannés')).toBeTruthy();
    // Top "Envoyer →" is hidden in receipt mode — the preview's Send button
    // is the commit. (AF-09)
    expect(queryByTestId('btn-send')).toBeNull();
    expect(getByText(/Ticket/)).toBeTruthy();
    expect(getByText(/Étiquette/)).toBeTruthy();
    expect(getByTestId('scan-capture-btn')).toBeTruthy();
  });

  it('shows top "Envoyer →" only in label mode', async () => {
    const { getByText, getByTestId, queryByTestId } = renderWithQuery(<ScanScreen />);
    // Default = receipt → no top send button
    expect(queryByTestId('btn-send')).toBeNull();
    // Switch to label → button appears
    fireEvent.press(getByText(/Étiquette/));
    await waitFor(() => expect(getByTestId('btn-send')).toBeTruthy());
  });

  it('starts in receipt mode (no counter)', () => {
    const { queryByTestId } = renderWithQuery(<ScanScreen />);
    expect(queryByTestId('photo-counter')).toBeNull();
  });

  it('displays scan history entries projected to the overlay shape', () => {
    const { getByText } = renderWithQuery(<ScanScreen />);
    // Receipt entries are shown by store_name + formatted total_amount.
    expect(getByText('Carrefour')).toBeTruthy();
    expect(getByText('1.25€')).toBeTruthy();
  });

  it('navigates to /scan-history when the "Voir tout" overlay button is pressed', () => {
    const { getByText } = renderWithQuery(<ScanScreen />);
    fireEvent.press(getByText('Voir tout →'));
    expect(mockRouterPush).toHaveBeenCalledWith('/scan-history');
  });

  // AF-09 — receipt capture must NOT touch the network until the user
  // confirms the photo in the preview screen.
  it('receipt shutter shows preview without enqueueing (AF-09)', async () => {
    const { getByTestId } = renderWithQuery(<ScanScreen />);
    fireEvent.press(getByTestId('scan-capture-btn'));
    await waitFor(() => expect(mockTakePictureAsync).toHaveBeenCalled());
    // Preview modal appears…
    await waitFor(() => expect(getByTestId('receipt-preview-image')).toBeTruthy());
    // …but no enqueue happens before confirm
    expect(mockEnqueueReceipt).not.toHaveBeenCalled();
    expect(mockEnqueueLabel).not.toHaveBeenCalled();
  });

  it('confirming the receipt preview enqueues and dismisses (AF-09)', async () => {
    const { getByTestId, queryByTestId } = renderWithQuery(<ScanScreen />);
    fireEvent.press(getByTestId('scan-capture-btn'));
    await waitFor(() => expect(getByTestId('receipt-preview-image')).toBeTruthy());

    fireEvent.press(getByTestId('receipt-preview-send'));
    await waitFor(() =>
      expect(mockEnqueueReceipt).toHaveBeenCalledWith('file:///mock.jpg'),
    );
    // Preview gone after confirm
    await waitFor(() => expect(queryByTestId('receipt-preview-image')).toBeNull());
  });

  it('retaking the receipt preview discards without enqueue (AF-09)', async () => {
    const { getByTestId, queryByTestId } = renderWithQuery(<ScanScreen />);
    fireEvent.press(getByTestId('scan-capture-btn'));
    await waitFor(() => expect(getByTestId('receipt-preview-image')).toBeTruthy());

    fireEvent.press(getByTestId('receipt-preview-retake'));
    await waitFor(() => expect(queryByTestId('receipt-preview-image')).toBeNull());
    expect(mockEnqueueReceipt).not.toHaveBeenCalled();
  });

  it('shows the unknown-store modal when a batch is uploaded without a matched store', async () => {
    const { scanEvents } = require('@/services/scan-events') as typeof import('@/services/scan-events');
    scanEvents._clearAll();

    const { getByTestId, queryByTestId } = renderWithQuery(<ScanScreen />);
    expect(queryByTestId('unknown-store-modal-scan-receipt')).toBeNull();

    await act(async () => {
      scanEvents.emit({ type: 'batch_uploaded', store_status: 'unknown' });
    });

    await waitFor(() => {
      expect(getByTestId('unknown-store-modal-scan-receipt')).toBeTruthy();
      expect(getByTestId('unknown-store-modal-later')).toBeTruthy();
    });
  });

  it('does not show the modal when store_status is confirmed', async () => {
    const { scanEvents } = require('@/services/scan-events') as typeof import('@/services/scan-events');
    scanEvents._clearAll();

    const { queryByTestId } = renderWithQuery(<ScanScreen />);

    await act(async () => {
      scanEvents.emit({ type: 'batch_uploaded', store_status: 'confirmed' });
    });

    expect(queryByTestId('unknown-store-modal-scan-receipt')).toBeNull();
  });

  it('"Plus tard" dismisses the unknown-store modal', async () => {
    const { scanEvents } = require('@/services/scan-events') as typeof import('@/services/scan-events');
    scanEvents._clearAll();

    const { getByTestId, queryByTestId } = renderWithQuery(<ScanScreen />);

    await act(async () => {
      scanEvents.emit({ type: 'batch_uploaded', store_status: 'unknown' });
    });
    await waitFor(() => expect(getByTestId('unknown-store-modal-later')).toBeTruthy());

    fireEvent.press(getByTestId('unknown-store-modal-later'));

    await waitFor(() =>
      expect(queryByTestId('unknown-store-modal-later')).toBeNull(),
    );
  });

  // ────────────────────────────────────────────────────────────────────
  // Bug fix 2026-05-01 — "Traitement en cours" badge for pending receipts
  // ────────────────────────────────────────────────────────────────────
  describe('pending-receipt overlay badge', () => {
    afterEach(() => {
      // Reset the entries ref so other tests in this file see the default
      // confirmed-receipt fixture again.
      __mockEntriesRef.current = [
        {
          type: 'receipt',
          receipt_id: '11111111-1111-1111-1111-111111111111',
          scanned_at: '2026-04-20T12:00:00+00:00',
          store_name: 'Carrefour',
          store_status: 'confirmed',
          total_amount_cents: 125,
          matched_count: 1,
          unmatched_count: 0,
          pending_count: 0,
        },
      ];
    });

    it('renders the "Analyse en cours" badge for a backend receipt with pending_count > 0', () => {
      // Pipeline_v3 creates pending scan rows BEFORE resolving them — without
      // surfacing pending_count, this row used to render as a misleading 0€
      // done-row instead of "Traitement en cours".
      __mockEntriesRef.current = [
        {
          type: 'receipt',
          receipt_id: '22222222-2222-2222-2222-222222222222',
          scanned_at: '2026-04-30T18:00:00+00:00',
          store_name: 'Auchan',
          store_status: 'confirmed',
          total_amount_cents: null,
          matched_count: 0,
          unmatched_count: 0,
          pending_count: 5,
        },
      ];
      const { getByTestId } = renderWithQuery(<ScanScreen />);
      expect(
        getByTestId('scan-history-status-processing-r:22222222-2222-2222-2222-222222222222'),
      ).toBeTruthy();
    });

    it('still renders processing for an empty backend receipt (no items, no total)', () => {
      __mockEntriesRef.current = [
        {
          type: 'receipt',
          receipt_id: '33333333-3333-3333-3333-333333333333',
          scanned_at: '2026-04-30T18:00:00+00:00',
          store_name: 'Auchan',
          store_status: 'confirmed',
          total_amount_cents: null,
          matched_count: 0,
          unmatched_count: 0,
          pending_count: 0,
        },
      ];
      const { getByTestId } = renderWithQuery(<ScanScreen />);
      expect(
        getByTestId('scan-history-status-processing-r:33333333-3333-3333-3333-333333333333'),
      ).toBeTruthy();
    });

    it('renders normal price row when the receipt has resolved (no pending, total set)', () => {
      // Already covered by the default fixture, but kept here as an explicit
      // anti-regression for the pending → done transition.
      const { queryByTestId } = renderWithQuery(<ScanScreen />);
      expect(
        queryByTestId('scan-history-status-processing-r:11111111-1111-1111-1111-111111111111'),
      ).toBeNull();
    });

    it('does not surface stale local error orphans in the abridged preview (fix 2026-05-01)', () => {
      // Bug : AsyncStorage scan history is never purged. Old `error` items
      // from past failed uploads accumulated indefinitely and were
      // unconditionally prepended to the overlay list, pushing recent
      // backend receipts off the visible 3-row slice. Symptom reported by
      // PO : "the preview shows only old failures while the full /scan-history
      // page shows my recent scans". Fix : LOCAL_ORPHAN_MAX_AGE_MS bound.
      const elevenMinAgo = Date.now() - 11 * 60 * 1_000;
      __mockPendingRef.current = [
        // 4 stale errors older than the recency window — must be ignored.
        { id: 'old-1', type: 'receipt', status: 'error', createdAt: elevenMinAgo },
        { id: 'old-2', type: 'receipt', status: 'error', createdAt: elevenMinAgo - 1_000 },
        { id: 'old-3', type: 'label',   status: 'error', createdAt: elevenMinAgo - 2_000 },
        { id: 'old-4', type: 'receipt', status: 'error', createdAt: elevenMinAgo - 3_000 },
      ];
      __mockEntriesRef.current = [
        {
          type: 'receipt',
          receipt_id: '44444444-4444-4444-4444-444444444444',
          scanned_at: '2026-04-30T18:00:00+00:00',
          store_name: 'Carrefour',
          store_status: 'confirmed',
          total_amount_cents: 1599,
          matched_count: 3,
          unmatched_count: 0,
          pending_count: 0,
        },
      ];
      const { queryByTestId, getByTestId } = renderWithQuery(<ScanScreen />);
      // Recent backend receipt must be visible…
      expect(
        getByTestId('scan-history-row-r:44444444-4444-4444-4444-444444444444'),
      ).toBeTruthy();
      // …and stale error orphans must NOT.
      expect(queryByTestId('scan-history-row-r:old-1')).toBeNull();
      expect(queryByTestId('scan-history-row-r:old-2')).toBeNull();
      expect(queryByTestId('scan-history-row-l:old-3')).toBeNull();
      expect(queryByTestId('scan-history-row-r:old-4')).toBeNull();
      __mockPendingRef.current = [];
    });

    it('still surfaces a recent uploading orphan (in-flight feedback)', () => {
      // Inverse guard : a *recent* in-flight `uploading` row stays visible —
      // that's the legit AF-02 behaviour, the user must see immediate
      // feedback while the queue is talking to the backend.
      const tenSecAgo = Date.now() - 10_000;
      __mockPendingRef.current = [
        { id: 'fresh', type: 'receipt', status: 'uploading', createdAt: tenSecAgo },
      ];
      __mockEntriesRef.current = [];
      const { getByTestId } = renderWithQuery(<ScanScreen />);
      expect(getByTestId('scan-history-row-r:fresh')).toBeTruthy();
      __mockPendingRef.current = [];
    });

    it('invalidates the scan-history query when a batch_uploaded event fires', async () => {
      // The scan tab subscribes to the queue's `batch_uploaded` event and
      // invalidates the cached history so the freshly-uploaded ticket appears
      // without pull-to-refresh. We assert the invalidate was called with the
      // shared SCAN_HISTORY_QUERY_KEY.
      const { scanEvents } = require('@/services/scan-events') as typeof import('@/services/scan-events');
      scanEvents._clearAll();

      // Spy on QueryClient.invalidateQueries via the wrapper's qc instance.
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');
      render(
        <QueryClientProvider client={qc}>
          <ScanScreen />
        </QueryClientProvider>,
      );

      await act(async () => {
        scanEvents.emit({ type: 'batch_uploaded', store_status: 'confirmed' });
      });

      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ['scan-history'] }),
      );
    });
  });
});

describe('ScanScreen — location permission', () => {
  it('renders the LocationPermissionBanner when permission is denied', async () => {
    const Location = require('expo-location');
    (Location.requestForegroundPermissionsAsync as jest.Mock).mockResolvedValueOnce({
      status: 'denied',
    });

    const { findByTestId } = renderWithQuery(<ScanScreen />);
    expect(await findByTestId('location-permission-banner')).toBeTruthy();
  });
});
