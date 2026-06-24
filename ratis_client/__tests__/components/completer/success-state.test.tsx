import { render, fireEvent } from '@testing-library/react-native';
import React from 'react';

import { SuccessState } from '@/components/completer/success-state';

describe('SuccessState', () => {
  it('renders reward as "+N ⚡"', () => {
    const { getByText } = render(
      <SuccessState reward={5} onNext={jest.fn()} onDone={jest.fn()} />,
    );
    expect(getByText('+5 ⚡')).toBeTruthy();
  });

  it('calls onNext on Suivant press', () => {
    const onNext = jest.fn();
    const { getByTestId } = render(
      <SuccessState reward={5} onNext={onNext} onDone={jest.fn()} />,
    );
    fireEvent.press(getByTestId('success-state-next'));
    expect(onNext).toHaveBeenCalled();
  });

  it('calls onDone on Retour press', () => {
    const onDone = jest.fn();
    const { getByTestId } = render(
      <SuccessState reward={5} onNext={jest.fn()} onDone={onDone} />,
    );
    fireEvent.press(getByTestId('success-state-done'));
    expect(onDone).toHaveBeenCalled();
  });
});
