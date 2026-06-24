import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn() },
}));

import { ScanHistoryLabelAccordion } from '@/components/scan/scan-history-label-accordion';
import { productClient } from '@/services/product-client';
import type { LabelGroupEntry } from '@/hooks/use-scan-history';

const ENTRY: LabelGroupEntry = {
  type: 'label_group',
  group_key: '22222222-2222-2222-2222-222222222222|2026-04-24',
  store_id: '22222222-2222-2222-2222-222222222222',
  date: '2026-04-24',
  store_name: 'Monoprix République',
  latest_scanned_at: '2026-04-24T09:30:00+00:00',
  accepted_count: 8,
};

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => jest.clearAllMocks());

describe('ScanHistoryLabelAccordion', () => {
  it('renders collapsed header with store name + accepted count', () => {
    const { getByText, queryByTestId } = renderWithQuery(
      <ScanHistoryLabelAccordion entry={ENTRY} />,
    );
    expect(getByText('MONOPRIX RÉPUBLIQUE')).toBeTruthy();
    expect(getByText(/8 produits pris en compte/)).toBeTruthy();
    expect(queryByTestId(`label-accordion-body-${ENTRY.group_key}`)).toBeNull();
    expect(productClient.get).not.toHaveBeenCalled();
  });

  it('lazy-fetches items on first expand', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({
      items: [
        {
          scan_id: 'lg-1',
          product_name: 'Yaourt Danone nature',
          product_ean: '3033490004057',
          price_cents: 115,
          match_method: 'barcode_ean',
          scanned_at: '2026-04-24T09:30:00+00:00',
        },
      ],
    });
    const { getByTestId, findByText } = renderWithQuery(
      <ScanHistoryLabelAccordion entry={ENTRY} />,
    );
    fireEvent.press(getByTestId(`label-accordion-header-${ENTRY.group_key}`));
    expect(await findByText('Yaourt Danone nature')).toBeTruthy();
    expect(productClient.get).toHaveBeenCalledWith(
      `/scan/label-group?store_id=${ENTRY.store_id}&date=${ENTRY.date}`,
    );
  });

  it('does not render a Rescanner button (labels = fire-and-forget)', () => {
    const { queryByText } = renderWithQuery(
      <ScanHistoryLabelAccordion entry={ENTRY} />,
    );
    expect(queryByText('Rescanner')).toBeNull();
  });

  it('displays the latest scan date AND time next to the store name', () => {
    // Bug-fix 2026-05-01 — label-group rows now include time of day so the
    // user can distinguish two label sessions on the same day. Pin an old
    // date so the assertion is deterministic across CI clocks.
    const entry: LabelGroupEntry = { ...ENTRY, latest_scanned_at: '2020-04-26T12:00:00Z' };
    const { getByTestId } = renderWithQuery(<ScanHistoryLabelAccordion entry={entry} />);
    const dateNode = getByTestId(`label-accordion-date-${entry.group_key}`);
    expect(dateNode.props.children.join('')).toMatch(/26\/04 \d{2}:\d{2}/);
  });

  it('does not render an edit-store pen (label groups are by-construction store-confirmed)', () => {
    const { queryByTestId } = renderWithQuery(<ScanHistoryLabelAccordion entry={ENTRY} />);
    // Pen icons live under `receipt-accordion-edit-store-*` testIDs only on receipts.
    expect(queryByTestId(/^receipt-accordion-edit-store-/)).toBeNull();
  });
});
