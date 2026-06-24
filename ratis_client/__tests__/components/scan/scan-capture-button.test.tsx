import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

import { ScanCaptureButton } from '@/components/scan/scan-capture-button';

describe('ScanCaptureButton', () => {
  it('calls onPress when tapped', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(<ScanCaptureButton onPress={onPress} />);
    fireEvent.press(getByTestId('scan-capture-btn'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not call onPress when disabled', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <ScanCaptureButton onPress={onPress} disabled />,
    );
    fireEvent.press(getByTestId('scan-capture-btn'));
    expect(onPress).not.toHaveBeenCalled();
  });

  it('has reduced opacity when disabled', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <ScanCaptureButton onPress={onPress} disabled />,
    );
    const btn = getByTestId('scan-capture-btn');
    const style = Array.isArray(btn.props.style)
      ? Object.assign({}, ...btn.props.style.filter(Boolean))
      : btn.props.style;
    expect(style.opacity).toBe(0.4);
  });
});
