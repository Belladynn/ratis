// ratis_client/__tests__/components/dashboard/buffer-confirm-modal.test.tsx
//
// Buffer + Burst (refonte 2026-05-09) — confirmation modal tests.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

import { BufferConfirmModal } from '@/components/dashboard/buffer-confirm-modal';

describe('BufferConfirmModal', () => {
  it('renders confirm + cancel buttons when open', () => {
    const { getByTestId } = render(
      <BufferConfirmModal
        open
        onClose={jest.fn()}
        onConfirm={jest.fn()}
        cabBonus={100}
        currentBufferCount={0}
      />,
    );
    expect(getByTestId('buffer-confirm-modal-confirm')).toBeTruthy();
    expect(getByTestId('buffer-confirm-modal-cancel')).toBeTruthy();
  });

  it('calls onConfirm when confirm pressed', () => {
    const onConfirm = jest.fn();
    const { getByTestId } = render(
      <BufferConfirmModal
        open
        onClose={jest.fn()}
        onConfirm={onConfirm}
        cabBonus={100}
        currentBufferCount={0}
      />,
    );
    fireEvent.press(getByTestId('buffer-confirm-modal-confirm'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when cancel pressed', () => {
    const onClose = jest.fn();
    const { getByTestId } = render(
      <BufferConfirmModal
        open
        onClose={onClose}
        onConfirm={jest.fn()}
        cabBonus={100}
        currentBufferCount={0}
      />,
    );
    fireEvent.press(getByTestId('buffer-confirm-modal-cancel'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('disables both buttons while loading', () => {
    const onConfirm = jest.fn();
    const onClose = jest.fn();
    const { getByTestId } = render(
      <BufferConfirmModal
        open
        loading
        onClose={onClose}
        onConfirm={onConfirm}
        cabBonus={100}
        currentBufferCount={0}
      />,
    );
    fireEvent.press(getByTestId('buffer-confirm-modal-confirm'));
    fireEvent.press(getByTestId('buffer-confirm-modal-cancel'));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
