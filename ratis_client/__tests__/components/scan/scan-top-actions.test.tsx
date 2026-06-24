import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

import { ScanTopActions } from '@/components/scan/scan-top-actions';

describe('ScanTopActions', () => {
  it('renders counter in label mode', () => {
    const { getByText } = render(
      <ScanTopActions mode="label" photoCount={3} maxPhotos={50} onSend={jest.fn()} />,
    );
    expect(getByText('3/50')).toBeTruthy();
  });

  it('hides counter in receipt mode', () => {
    const { queryByTestId } = render(
      <ScanTopActions mode="receipt" photoCount={0} maxPhotos={50} onSend={jest.fn()} />,
    );
    expect(queryByTestId('photo-counter')).toBeNull();
  });

  it('disables send button when photoCount=0', () => {
    const { getByTestId } = render(
      <ScanTopActions mode="label" photoCount={0} maxPhotos={50} onSend={jest.fn()} />,
    );
    expect(getByTestId('btn-send').props.accessibilityState?.disabled).toBe(true);
  });

  it('calls onSend when enabled and pressed', () => {
    const onSend = jest.fn();
    const { getByTestId } = render(
      <ScanTopActions mode="label" photoCount={3} maxPhotos={50} onSend={onSend} />,
    );
    fireEvent.press(getByTestId('btn-send'));
    expect(onSend).toHaveBeenCalled();
  });
});
