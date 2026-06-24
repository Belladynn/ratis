import { render, fireEvent } from '@testing-library/react-native';
import React from 'react';

import { FieldInputText } from '@/components/completer/field-input-text';

const baseTask = {
  product_ean: '9990000000001',
  product_name: 'Lait',
  missing_field: 'brands' as const,
  cab_reward: 5,
};

describe('FieldInputText', () => {
  it('submit button disabled when input empty', () => {
    const onSubmit = jest.fn();
    const { getByTestId } = render(
      <FieldInputText task={baseTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.press(getByTestId('field-input-text-submit'));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submit button disabled when input < 2 chars', () => {
    const onSubmit = jest.fn();
    const { getByTestId } = render(
      <FieldInputText task={baseTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.changeText(getByTestId('field-input-text-input'), 'a');
    fireEvent.press(getByTestId('field-input-text-submit'));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('calls onSubmit with trimmed value', () => {
    const onSubmit = jest.fn();
    const { getByTestId } = render(
      <FieldInputText task={baseTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.changeText(getByTestId('field-input-text-input'), '  Lactel  ');
    fireEvent.press(getByTestId('field-input-text-submit'));
    expect(onSubmit).toHaveBeenCalledWith('Lactel');
  });

  it('calls onSkip when skip button pressed', () => {
    const onSkip = jest.fn();
    const { getByTestId } = render(
      <FieldInputText task={baseTask} onSubmit={jest.fn()} onSkip={onSkip} />,
    );
    fireEvent.press(getByTestId('field-input-text-skip'));
    expect(onSkip).toHaveBeenCalled();
  });
});
