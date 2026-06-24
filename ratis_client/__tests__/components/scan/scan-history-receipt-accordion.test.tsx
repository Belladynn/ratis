import React from 'react';
import { render, fireEvent, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ScanHistoryReceiptAccordion } from '@/components/scan/scan-history-receipt-accordion';
import { productClient } from '@/services/product-client';
import type { ReceiptEntry } from '@/hooks/use-scan-history';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockRouterPush }),
}));

const ENTRY: ReceiptEntry = {
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

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('ScanHistoryReceiptAccordion', () => {
  it('renders collapsed header with store name and article count', () => {
    const { getByText, queryByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    expect(getByText('CARREFOUR MÉNILMONTANT')).toBeTruthy();
    expect(getByText(/12 articles/)).toBeTruthy();
    // Body not mounted until expanded
    expect(queryByTestId('receipt-accordion-body-11111111-1111-1111-1111-111111111111')).toBeNull();
    // Backend should NOT have been hit — lazy fetch
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('fetches items on first expand and renders item rows', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      status: 'done',
      matched: 1,
      unmatched: 0,
      total_amount: 129,
      store_status: 'confirmed',
      pending_items_count: 0,
      items: [
        {
          scan_id: 'item-1',
          scanned_name: 'LAIT DE DE-ECR',
          product_name: 'Lait demi-écrémé 1L',
          product_ean: '3428270000019',
          quantity: 1,
          price_cents: 129,
          status: 'accepted',
          match_method: 'barcode_ean',
        },
      ],
    });
    const { getByTestId, findByText } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-header-11111111-1111-1111-1111-111111111111'));
    expect(await findByText('Lait demi-écrémé 1L')).toBeTruthy();
    expect(productClient.get).toHaveBeenCalledWith(
      '/scan/receipt/11111111-1111-1111-1111-111111111111',
    );
  });

  it('tapping the Rescanner button navigates to /(tabs)/scan', () => {
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-rescan-11111111-1111-1111-1111-111111111111'));
    expect(mockRouterPush).toHaveBeenCalledWith('/(tabs)/scan');
  });

  it('forwards a barcode-button tap to the parent with the full item', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      status: 'done',
      matched: 0,
      unmatched: 1,
      total_amount: 150,
      store_status: 'confirmed',
      pending_items_count: 0,
      items: [
        {
          scan_id: 'item-red',
          scanned_name: 'PATE A TART FERRE',
          product_name: null,
          product_ean: null,
          quantity: 1,
          price_cents: 299,
          status: 'unmatched',
          match_method: null,
        },
      ],
    });
    const onPressBarcodeForItem = jest.fn();
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={onPressBarcodeForItem} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-header-11111111-1111-1111-1111-111111111111'));
    await waitFor(() => expect(productClient.get).toHaveBeenCalled());
    const btn = await waitFor(() => getByTestId('scan-history-item-barcode-item-red'));
    await act(async () => {
      fireEvent.press(btn);
    });
    expect(onPressBarcodeForItem).toHaveBeenCalledWith(
      expect.objectContaining({ scan_id: 'item-red', status: 'unmatched' }),
    );
  });

  it('displays the receipt date AND time next to the store name', () => {
    // Bug-fix 2026-05-01 — receipt rows now include time of day so the user
    // can distinguish two scans on the same day. The accordion delegates to
    // `formatScanDateTime` which returns "Aujourd'hui HH:MM", "Hier HH:MM"
    // or "DD/MM HH:MM". Picking an old date here keeps the assertion
    // deterministic regardless of the test-runner clock.
    const entry: ReceiptEntry = { ...ENTRY, scanned_at: '2020-04-27T15:30:00Z' };
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={entry} onPressBarcodeForItem={jest.fn()} />,
    );
    const dateNode = getByTestId(`receipt-accordion-date-${entry.receipt_id}`);
    // We don't pin the local-tz hour:minute (CI runs UTC, dev may not), but we
    // assert the shape: "DD/MM HH:MM" with two-digit day, month, hour, minute.
    expect(dateNode.props.children.join('')).toMatch(/27\/04 \d{2}:\d{2}/);
  });

  it('hides the date (and its separator) when scanned_at is null', () => {
    const entry: ReceiptEntry = { ...ENTRY, scanned_at: null };
    const { queryByTestId, getByText } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={entry} onPressBarcodeForItem={jest.fn()} />,
    );
    // Store still rendered, just no date suffix.
    expect(getByText('CARREFOUR MÉNILMONTANT')).toBeTruthy();
    expect(queryByTestId(`receipt-accordion-date-${entry.receipt_id}`)).toBeNull();
  });

  it('renders the edit-store pen in red when store_status is unknown', () => {
    const entry: ReceiptEntry = { ...ENTRY, store_status: 'unknown', store_name: null };
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={entry} onPressBarcodeForItem={jest.fn()} />,
    );
    const pen = getByTestId(`receipt-accordion-edit-store-${entry.receipt_id}`);
    // The glyph child carries the colour; flatten its style.
    const glyph = pen.findByType('Text' as never);
    const flat = (Array.isArray(glyph.props.style) ? Object.assign({}, ...glyph.props.style) : glyph.props.style) as { color?: string };
    expect(flat.color).toBe('#EF4444');
  });

  it('renders the edit-store pen in red when store_status is pending', () => {
    const entry: ReceiptEntry = { ...ENTRY, store_status: 'pending' };
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={entry} onPressBarcodeForItem={jest.fn()} />,
    );
    const pen = getByTestId(`receipt-accordion-edit-store-${entry.receipt_id}`);
    const glyph = pen.findByType('Text' as never);
    const flat = (Array.isArray(glyph.props.style) ? Object.assign({}, ...glyph.props.style) : glyph.props.style) as { color?: string };
    expect(flat.color).toBe('#EF4444');
  });

  it('renders the edit-store pen in grey when the store is confirmed', () => {
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    const pen = getByTestId(`receipt-accordion-edit-store-${ENTRY.receipt_id}`);
    const glyph = pen.findByType('Text' as never);
    const flat = (Array.isArray(glyph.props.style) ? Object.assign({}, ...glyph.props.style) : glyph.props.style) as { color?: string };
    expect(flat.color).toBe('rgba(255,255,255,0.4)');
  });

  it('forwards a pen tap to the parent via onPressEditStore with the entry', () => {
    const onPressEditStore = jest.fn();
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion
        entry={ENTRY}
        onPressBarcodeForItem={jest.fn()}
        onPressEditStore={onPressEditStore}
      />,
    );
    fireEvent.press(getByTestId(`receipt-accordion-edit-store-${ENTRY.receipt_id}`));
    expect(onPressEditStore).toHaveBeenCalledTimes(1);
    expect(onPressEditStore).toHaveBeenCalledWith(ENTRY);
  });

  it('renders the "pending validation" badge when store_status is pending', () => {
    const entry: ReceiptEntry = { ...ENTRY, store_status: 'pending' };
    const { getByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={entry} onPressBarcodeForItem={jest.fn()} />,
    );
    expect(
      getByTestId(`receipt-accordion-pending-validation-${entry.receipt_id}`),
    ).toBeTruthy();
  });

  it('does not render the "pending validation" badge when store is confirmed', () => {
    const { queryByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    expect(
      queryByTestId(`receipt-accordion-pending-validation-${ENTRY.receipt_id}`),
    ).toBeNull();
  });

  // -- pipeline-v3 status rendering (Bloc 9) -----------------------------
  it('renders pipeline-v3 matched item as green without rejected_reason', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      status: 'done',
      matched: 1,
      unmatched: 0,
      total_amount: 199,
      store_status: 'confirmed',
      pending_items_count: 0,
      items: [
        {
          scan_id: 'item-v3-matched',
          // Equal scanned_name & product_name → no OCR-brut subtitle either,
          // so we can assert "no subtitle" cleanly.
          scanned_name: 'Coca-Cola 33cl',
          product_name: 'Coca-Cola 33cl',
          product_ean: '5000000000019',
          quantity: 1,
          price_cents: 199,
          status: 'matched',
          match_method: 'barcode',
          rejected_reason: null,
        },
      ],
    });
    const { getByTestId, findByText, queryByTestId, queryByText } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-header-11111111-1111-1111-1111-111111111111'));
    expect(await findByText('Coca-Cola 33cl')).toBeTruthy();
    // No rejected_reason subtitle should appear.
    await waitFor(() =>
      expect(queryByTestId('scan-history-item-subtitle-item-v3-matched')).toBeNull(),
    );
    // No "Non identifié" / "Aucun produit similaire" / etc. visible.
    expect(queryByText('Non identifié')).toBeNull();
  });

  it('renders pipeline-v3 unresolved item with translated rejected_reason subtitle', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      status: 'done',
      matched: 0,
      unmatched: 1,
      total_amount: 235,
      store_status: 'confirmed',
      pending_items_count: 0,
      items: [
        {
          scan_id: 'item-v3-unresolved',
          scanned_name: 'YGRT NAT 4X125G',
          product_name: null,
          product_ean: null,
          quantity: 1,
          price_cents: 235,
          status: 'unresolved',
          match_method: null,
          rejected_reason: 'no_fuzzy_candidate',
        },
      ],
    });
    const { getByTestId, findByText } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-header-11111111-1111-1111-1111-111111111111'));
    expect(await findByText('YGRT NAT 4X125G')).toBeTruthy();
    expect(await findByText('Aucun produit similaire trouvé')).toBeTruthy();
  });

  it('renders pipeline-v3 rejected item with red glyph and reason (defensive)', async () => {
    // Backend currently filters rejected from /scan/receipt/{id}, but the
    // frontend renders defensively in case a row sneaks through (e.g. admin
    // un-rejection pending re-filter, or future product decision to surface
    // them). See utils/scan-status.ts § REJECTED_UX.
    (productClient.get as jest.Mock).mockResolvedValue({
      status: 'done',
      matched: 0,
      unmatched: 0,
      total_amount: null,
      store_status: 'confirmed',
      pending_items_count: 0,
      items: [
        {
          scan_id: 'item-v3-rejected',
          scanned_name: 'GARBAGE TEXT',
          product_name: null,
          product_ean: null,
          quantity: 1,
          price_cents: null,
          status: 'rejected',
          match_method: null,
          rejected_reason: 'ocr_garbage',
        },
      ],
    });
    const { getByTestId, findByText } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-header-11111111-1111-1111-1111-111111111111'));
    expect(await findByText('GARBAGE TEXT')).toBeTruthy();
    expect(await findByText('Texte illisible sur le ticket')).toBeTruthy();
  });

  it('legacy v2 accepted item renders identically to v3 matched (backward compat)', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      status: 'done',
      matched: 1,
      unmatched: 0,
      total_amount: 489,
      store_status: 'confirmed',
      pending_items_count: 0,
      items: [
        {
          scan_id: 'item-legacy',
          scanned_name: 'NUTELLA 400G',
          product_name: 'Nutella 400g',
          product_ean: '3017620429484',
          quantity: 1,
          price_cents: 489,
          // v2 shape: `accepted` + `barcode_ean` (pre pipeline_v3 rollout).
          status: 'accepted',
          match_method: 'barcode_ean',
        },
      ],
    });
    const { getByTestId, findByText, queryByTestId } = renderWithQuery(
      <ScanHistoryReceiptAccordion entry={ENTRY} onPressBarcodeForItem={jest.fn()} />,
    );
    fireEvent.press(getByTestId('receipt-accordion-header-11111111-1111-1111-1111-111111111111'));
    expect(await findByText('Nutella 400g')).toBeTruthy();
    // Legacy accepted should NOT show a rejected_reason subtitle (matched → no reason).
    await waitFor(() =>
      expect(queryByTestId('scan-history-item-subtitle-item-legacy')).toBeTruthy(),
    );
    // The subtitle here is the OCR-brut hint (scanned_name differs from product_name)
    // — same UX as before. We assert it's NOT a rejected_reason translation.
    expect(await findByText(/OCR: NUTELLA 400G/)).toBeTruthy();
  });
});
