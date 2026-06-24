import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { ScanModeSwitch } from '@/components/scan/scan-mode-switch';

describe('ScanModeSwitch', () => {
  it('renders both modes', () => {
    const { getByText } = render(
      <ScanModeSwitch mode="receipt" onChange={jest.fn()} />,
    );
    expect(getByText(/Ticket/)).toBeTruthy();
    expect(getByText(/Étiquette/)).toBeTruthy();
  });

  it('calls onChange with "label" when tapping Étiquette', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <ScanModeSwitch mode="receipt" onChange={onChange} />,
    );
    fireEvent.press(getByTestId('scan-mode-label'));
    expect(onChange).toHaveBeenCalledWith('label');
  });

  it('calls onChange with "receipt" when tapping Ticket', () => {
    const onChange = jest.fn();
    const { getByTestId } = render(
      <ScanModeSwitch mode="label" onChange={onChange} />,
    );
    fireEvent.press(getByTestId('scan-mode-receipt'));
    expect(onChange).toHaveBeenCalledWith('receipt');
  });
});
