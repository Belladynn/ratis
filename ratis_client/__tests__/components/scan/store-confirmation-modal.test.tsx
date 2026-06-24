import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';

import { StoreConfirmationModal } from '@/components/scan/store-confirmation-modal';

const FULL_INFO = {
  brand_guess: 'Lidl',
  address: '12 RUE DE LA PAIX',
  postal_code: '75002',
  city: 'PARIS',
  phone: '0142345678',
};

describe('StoreConfirmationModal', () => {
  beforeEach(() => {
    jest.useRealTimers();
  });

  it('renders all candidate fields when fully populated', () => {
    const { getByText } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={jest.fn()}
      />,
    );
    expect(getByText('Magasin inconnu de nos services')).toBeTruthy();
    expect(getByText(/LIDL/)).toBeTruthy();
    expect(getByText(/12 RUE DE LA PAIX/)).toBeTruthy();
    expect(getByText(/75002/)).toBeTruthy();
    expect(getByText(/PARIS/)).toBeTruthy();
    expect(getByText(/0142345678/)).toBeTruthy();
  });

  it('skips null fields gracefully (no empty rows)', () => {
    const { getByText, queryByText } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={{
          brand_guess: 'Lidl',
          address: null,
          postal_code: null,
          city: null,
          phone: null,
        }}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={jest.fn()}
      />,
    );
    expect(getByText(/LIDL/)).toBeTruthy();
    // No phone glyph row when phone is null
    expect(queryByText(/☎️/)).toBeNull();
    // No address row when address is null
    expect(queryByText(/📍/)).toBeNull();
  });

  it('calls onConfirm exactly once when the Confirm button is pressed', () => {
    const onConfirm = jest.fn();
    const { getByTestId } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={onConfirm}
        onClose={jest.fn()}
        onRescan={jest.fn()}
      />,
    );
    fireEvent.press(getByTestId('store-confirmation-confirm-btn'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('calls onRescan when the Re-scan button is pressed', () => {
    const onRescan = jest.fn();
    const { getByTestId } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={onRescan}
      />,
    );
    fireEvent.press(getByTestId('store-confirmation-rescan-btn'));
    expect(onRescan).toHaveBeenCalledTimes(1);
  });

  it('disables the Confirm button while isLoading=true', () => {
    const onConfirm = jest.fn();
    const { getByTestId } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={onConfirm}
        onClose={jest.fn()}
        onRescan={jest.fn()}
        isLoading
      />,
    );
    fireEvent.press(getByTestId('store-confirmation-confirm-btn'));
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('renders the insufficient_ocr_data error message', () => {
    const { getByText } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={jest.fn()}
        errorCode="insufficient_ocr_data"
      />,
    );
    expect(getByText(/insuffisantes/i)).toBeTruthy();
  });

  it('renders the candidate_not_found error message', () => {
    const { getByText } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={jest.fn()}
        errorCode="candidate_not_found"
      />,
    );
    expect(getByText(/lisible/i)).toBeTruthy();
  });

  it('renders the generic error message', () => {
    const { getByText } = render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={jest.fn()}
        errorCode="generic"
      />,
    );
    expect(getByText(/erreur/i)).toBeTruthy();
  });

  it('auto-closes after 1500ms when errorCode=receipt_already_resolved', async () => {
    jest.useFakeTimers();
    const onClose = jest.fn();
    render(
      <StoreConfirmationModal
        visible
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={onClose}
        onRescan={jest.fn()}
        errorCode="receipt_already_resolved"
      />,
    );
    expect(onClose).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(1500);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
  });

  it('does not render the confirm button when visible=false', () => {
    const { queryByTestId } = render(
      <StoreConfirmationModal
        visible={false}
        candidateInfo={FULL_INFO}
        onConfirm={jest.fn()}
        onClose={jest.fn()}
        onRescan={jest.fn()}
      />,
    );
    // RN's <Modal> in the test renderer skips its descendants when not visible.
    expect(queryByTestId('store-confirmation-confirm-btn')).toBeNull();
  });
});
