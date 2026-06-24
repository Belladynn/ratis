import { render, fireEvent } from '@testing-library/react-native';
import React from 'react';

import { FieldInputTags } from '@/components/completer/field-input-tags';

const categoriesTask = {
  product_ean: '9990000000001',
  product_name: 'Lait',
  missing_field: 'categories_tags' as const,
  cab_reward: 5,
};

const labelsTask = {
  ...categoriesTask,
  missing_field: 'labels_tags' as const,
};

describe('FieldInputTags', () => {
  it('renders categories curated chips for categories_tags', () => {
    const { getByText } = render(
      <FieldInputTags task={categoriesTask} onSubmit={jest.fn()} onSkip={jest.fn()} />,
    );
    expect(getByText('Produits laitiers')).toBeTruthy();
    expect(getByText('Fromages')).toBeTruthy();
  });

  it('renders labels curated chips for labels_tags', () => {
    const { getByText } = render(
      <FieldInputTags task={labelsTask} onSubmit={jest.fn()} onSkip={jest.fn()} />,
    );
    expect(getByText(/Bio/)).toBeTruthy();
    expect(getByText('Label Rouge')).toBeTruthy();
  });

  it('submit disabled when 0 tags selected', () => {
    const onSubmit = jest.fn();
    const { getByTestId } = render(
      <FieldInputTags task={categoriesTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.press(getByTestId('field-input-tags-submit'));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('toggles chip on press', () => {
    const onSubmit = jest.fn();
    const { getByText, getByTestId } = render(
      <FieldInputTags task={categoriesTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.press(getByText('Fromages'));
    fireEvent.press(getByTestId('field-input-tags-submit'));
    expect(onSubmit).toHaveBeenCalledWith(['en:cheeses']);
  });

  it('deselect a previously selected chip', () => {
    const onSubmit = jest.fn();
    const { getByText, getByTestId } = render(
      <FieldInputTags task={categoriesTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.press(getByText('Fromages'));
    fireEvent.press(getByText('Fromages')); // toggle off
    fireEvent.press(getByTestId('field-input-tags-submit'));
    // submit shouldn't be called — 0 tags selected = disabled
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits multiple selected tags', () => {
    const onSubmit = jest.fn();
    const { getByText, getByTestId } = render(
      <FieldInputTags task={categoriesTask} onSubmit={onSubmit} onSkip={jest.fn()} />,
    );
    fireEvent.press(getByText('Fromages'));
    fireEvent.press(getByText('Yaourts'));
    fireEvent.press(getByTestId('field-input-tags-submit'));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.arrayContaining(['en:cheeses', 'en:yogurts']),
    );
  });
});
