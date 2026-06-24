import { render } from '@testing-library/react-native';
import React from 'react';

import { FormState } from '@/components/completer/form-state';

const textTask = {
  product_ean: '9990000000001',
  product_name: 'Lait',
  missing_field: 'brands' as const,
  cab_reward: 5,
};

const tagsTask = { ...textTask, missing_field: 'categories_tags' as const };

describe('FormState', () => {
  it('renders FieldInputText for brands field', () => {
    const { getByTestId } = render(
      <FormState task={textTask} onSubmit={jest.fn()} onSkip={jest.fn()} />,
    );
    expect(getByTestId('field-input-text-input')).toBeTruthy();
  });

  it('renders FieldInputTags for categories_tags field', () => {
    const { queryByTestId } = render(
      <FormState task={tagsTask} onSubmit={jest.fn()} onSkip={jest.fn()} />,
    );
    expect(queryByTestId('field-input-text-input')).toBeNull();
    expect(queryByTestId('field-input-tags-submit')).toBeTruthy();
  });

  it('displays the product name', () => {
    const { getByText } = render(
      <FormState task={textTask} onSubmit={jest.fn()} onSkip={jest.fn()} />,
    );
    expect(getByText('Lait')).toBeTruthy();
  });
});
