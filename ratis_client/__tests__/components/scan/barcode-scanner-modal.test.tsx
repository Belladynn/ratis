import React from 'react';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';

jest.mock('expo-camera', () => ({
  CameraView: ({ onBarcodeScanned }: { onBarcodeScanned?: (p: { data: string }) => void }) => {
    (global as unknown as { __triggerBarcode?: (p: { data: string }) => void }).__triggerBarcode =
      onBarcodeScanned;
    return null;
  },
  useCameraPermissions: jest.fn(() => [{ granted: true }, jest.fn()]),
}));

jest.mock('expo-haptics', () => ({
  notificationAsync: jest.fn().mockResolvedValue(undefined),
  selectionAsync: jest.fn().mockResolvedValue(undefined),
  NotificationFeedbackType: { Success: 'success', Warning: 'warning', Error: 'error' },
}));

import { BarcodeScannerModal } from '@/components/scan/barcode-scanner-modal';
import { useCameraPermissions } from 'expo-camera';

beforeEach(() => {
  jest.clearAllMocks();
  (global as unknown as { __triggerBarcode?: unknown }).__triggerBarcode = undefined;
});

describe('BarcodeScannerModal', () => {
  it('renders the caller-provided title when visible', () => {
    const { getByText } = render(
      <BarcodeScannerModal
        visible
        onClose={jest.fn()}
        onBarcode={jest.fn()}
        title="Scanne le code-barre"
      />,
    );
    expect(getByText('Scanne le code-barre')).toBeTruthy();
  });

  it('calls onClose when the close button is pressed', () => {
    const onClose = jest.fn();
    const { getByTestId } = render(
      <BarcodeScannerModal
        visible
        onClose={onClose}
        onBarcode={jest.fn()}
        title="Scanne"
      />,
    );
    fireEvent.press(getByTestId('barcode-scanner-modal-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows permission-denied state when the camera permission is not granted', () => {
    (useCameraPermissions as jest.Mock).mockReturnValueOnce([{ granted: false }, jest.fn()]);
    const { getByTestId } = render(
      <BarcodeScannerModal
        visible
        onClose={jest.fn()}
        onBarcode={jest.fn()}
        title="Scanne"
      />,
    );
    expect(getByTestId('barcode-scanner-permission-denied')).toBeTruthy();
  });

  it('invokes onBarcode with the scanned EAN', async () => {
    const onBarcode = jest.fn().mockResolvedValue(undefined);
    render(
      <BarcodeScannerModal
        visible
        onClose={jest.fn()}
        onBarcode={onBarcode}
        title="Scanne"
      />,
    );
    await act(async () => {
      (global as unknown as { __triggerBarcode?: (p: { data: string }) => void }).__triggerBarcode?.(
        { data: '3428270000019' },
      );
    });
    await waitFor(() => expect(onBarcode).toHaveBeenCalledWith('3428270000019'));
  });

  it('de-duplicates repeated reads of the same EAN within the cooldown window', async () => {
    const onBarcode = jest.fn().mockResolvedValue(undefined);
    render(
      <BarcodeScannerModal
        visible
        onClose={jest.fn()}
        onBarcode={onBarcode}
        title="Scanne"
      />,
    );
    const trigger = () =>
      (global as unknown as { __triggerBarcode?: (p: { data: string }) => void })
        .__triggerBarcode?.({ data: '3428270000019' });
    await act(async () => {
      trigger();
      trigger();
      trigger();
    });
    await waitFor(() => expect(onBarcode).toHaveBeenCalledTimes(1));
  });

  it('does not call onBarcode after the modal is closed', async () => {
    const onBarcode = jest.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <BarcodeScannerModal
        visible
        onClose={jest.fn()}
        onBarcode={onBarcode}
        title="Scanne"
      />,
    );
    rerender(
      <BarcodeScannerModal
        visible={false}
        onClose={jest.fn()}
        onBarcode={onBarcode}
        title="Scanne"
      />,
    );
    await act(async () => {
      (global as unknown as { __triggerBarcode?: (p: { data: string }) => void })
        .__triggerBarcode?.({ data: '3428270000019' });
    });
    expect(onBarcode).not.toHaveBeenCalled();
  });
});
