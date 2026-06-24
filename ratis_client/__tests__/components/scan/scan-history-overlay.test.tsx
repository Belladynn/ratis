import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { ScanHistoryOverlay } from '@/components/scan/scan-history-overlay';

const ITEMS = [
  { id: '1', name: 'Lait Lactel 1L', price: 1.25 },
  { id: '2', name: 'Pain Harrys', price: 2.80 },
  { id: '3', name: 'Bananes bio', price: 2.90 },
  { id: '4', name: 'Hidden', price: 1.00 },
];

describe('ScanHistoryOverlay', () => {
  it('renders up to 3 items', () => {
    const { getByText, queryByText } = render(
      <ScanHistoryOverlay items={ITEMS} onPressMore={jest.fn()} />,
    );
    expect(getByText('Lait Lactel 1L')).toBeTruthy();
    expect(getByText('Pain Harrys')).toBeTruthy();
    expect(getByText('Bananes bio')).toBeTruthy();
    expect(queryByText('Hidden')).toBeNull();
  });

  it('calls onPressMore when tapping "Voir tout"', () => {
    const onPressMore = jest.fn();
    const { getByText } = render(
      <ScanHistoryOverlay items={ITEMS} onPressMore={onPressMore} />,
    );
    fireEvent.press(getByText('Voir tout →'));
    expect(onPressMore).toHaveBeenCalled();
  });

  it('renders empty state when no items', () => {
    const { getByText } = render(
      <ScanHistoryOverlay items={[]} onPressMore={jest.fn()} />,
    );
    expect(getByText('Historique produits scannés')).toBeTruthy();
  });

  it('renders a tappable "Scanner le ticket" chip instead of price for unknown_store items', () => {
    const { getByTestId, queryByText, getByText } = render(
      <ScanHistoryOverlay
        items={[
          { id: 'u1', name: 'Yaourt nature', price: 1.99, status: 'unknown_store' },
        ]}
        onPressMore={jest.fn()}
      />,
    );
    expect(getByTestId('scan-history-pending-badge-u1')).toBeTruthy();
    expect(getByText('Scanner le ticket →')).toBeTruthy();
    // Price hidden for unknown-store items — no CAB yet, no meaningful price to show
    expect(queryByText('1.99€')).toBeNull();
  });

  it('fires onRequestReceiptMode when the unknown-store chip is pressed', () => {
    const onRequestReceiptMode = jest.fn();
    const { getByTestId } = render(
      <ScanHistoryOverlay
        items={[
          { id: 'u1', name: 'Yaourt nature', price: 1.99, status: 'unknown_store' },
        ]}
        onPressMore={jest.fn()}
        onRequestReceiptMode={onRequestReceiptMode}
      />,
    );
    fireEvent.press(getByTestId('scan-history-pending-badge-u1'));
    expect(onRequestReceiptMode).toHaveBeenCalledTimes(1);
  });

  it('renders the normal price for items without unknown_store status', () => {
    const { getByText, queryByTestId } = render(
      <ScanHistoryOverlay
        items={[{ id: 'n1', name: 'Yaourt nature', price: 1.99 }]}
        onPressMore={jest.fn()}
      />,
    );
    expect(getByText('1.99€')).toBeTruthy();
    expect(queryByTestId('scan-history-pending-badge-n1')).toBeNull();
  });
});
