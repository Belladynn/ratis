// __tests__/components/liste/list-item-row.test.tsx
//
// Restored at chunk 4 of visual iso V5 reconstruction (PR feat/visual-iso-v5).

import React from 'react';
import { StyleSheet } from 'react-native';
import { render, fireEvent } from '@testing-library/react-native';
import { ListItemRow } from '@/components/liste/list-item-row';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

const item = {
  id: 'item-1',
  product_ean: '3428270000019',
  product_name: 'Lait demi-écrémé 1L',
  quantity: 1,
  checked: false,
  checked_at: null,
  // Wave 12 — ``category`` is part of the ShoppingListItem contract
  // now. Fixture defaults to ``null`` (no resolved product category)
  // so the existing assertions on the row's visual still apply ; the
  // visual ``category`` prop comes from the screen-level palette map.
  category: null,
};

describe('ListItemRow', () => {
  it('renders the product name', () => {
    const { getByText } = render(
      <ListItemRow item={item} onToggle={jest.fn()} onDelete={jest.fn()} />,
    );
    expect(getByText('Lait demi-écrémé 1L')).toBeTruthy();
  });

  it('renders quantity badge when quantity > 1', () => {
    const { getByText } = render(
      <ListItemRow
        item={{ ...item, quantity: 3 }}
        onToggle={jest.fn()}
        onDelete={jest.fn()}
      />,
    );
    expect(getByText('×3')).toBeTruthy();
  });

  it('does not render quantity badge when quantity is 1', () => {
    const { queryByText } = render(
      <ListItemRow item={item} onToggle={jest.fn()} onDelete={jest.fn()} />,
    );
    expect(queryByText('×1')).toBeNull();
  });

  it('calls onToggle when the checkbox row is pressed', () => {
    const onToggle = jest.fn();
    const { getByTestId } = render(
      <ListItemRow item={item} onToggle={onToggle} onDelete={jest.fn()} />,
    );
    fireEvent.press(getByTestId('list-item-row-toggle'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  // V5 strict iso (Liste Courses.png) — trash icon removed; delete is no
  // longer surfaced in V1 UI but `onDelete` prop is kept for V2 swipe.
  it('does not render an inline delete button (V5 strict iso)', () => {
    const { queryByTestId } = render(
      <ListItemRow item={item} onToggle={jest.fn()} onDelete={jest.fn()} />,
    );
    expect(queryByTestId('list-item-row-delete')).toBeNull();
  });

  it('renders the brand eyebrow when a brand is supplied', () => {
    const { getByText } = render(
      <ListItemRow
        item={item}
        brand="LACTEL"
        category="dairy"
        onToggle={jest.fn()}
        onDelete={jest.fn()}
      />,
    );
    expect(getByText('LACTEL')).toBeTruthy();
  });

  it('applies the checked style when item.checked is true', () => {
    const { getByText } = render(
      <ListItemRow
        item={{ ...item, checked: true }}
        onToggle={jest.fn()}
        onDelete={jest.fn()}
      />,
    );
    const name = getByText('Lait demi-écrémé 1L');
    const flat = Array.isArray(name.props.style)
      ? Object.assign({}, ...name.props.style.filter(Boolean))
      : name.props.style;
    expect(flat.textDecorationLine).toBe('line-through');
  });

  // V5 strict iso — items are rendered in a single block container with
  // internal hairline dividers. Each row paints a bottom divider EXCEPT
  // the last one (which sits on the container's bottom rounded edge).
  describe('single-block rendering (handoff iso)', () => {
    it('row has no per-row marginBottom (block container handles spacing)', () => {
      const { getByTestId } = render(
        <ListItemRow item={item} onToggle={jest.fn()} onDelete={jest.fn()} />,
      );
      const row = getByTestId('list-item-row');
      const flat = Array.isArray(row.props.style)
        ? Object.assign({}, ...row.props.style.filter(Boolean))
        : row.props.style;
      expect(flat.marginBottom ?? 0).toBe(0);
    });

    it('row has no borderRadius (block container handles corner rounding)', () => {
      const { getByTestId } = render(
        <ListItemRow item={item} onToggle={jest.fn()} onDelete={jest.fn()} />,
      );
      const row = getByTestId('list-item-row');
      const flat = Array.isArray(row.props.style)
        ? Object.assign({}, ...row.props.style.filter(Boolean))
        : row.props.style;
      expect(flat.borderRadius ?? 0).toBe(0);
    });

    it('renders a bottom hairline divider when isLast is not set', () => {
      const { getByTestId } = render(
        <ListItemRow item={item} onToggle={jest.fn()} onDelete={jest.fn()} />,
      );
      const row = getByTestId('list-item-row');
      const flat = Array.isArray(row.props.style)
        ? Object.assign({}, ...row.props.style.filter(Boolean))
        : row.props.style;
      expect(flat.borderBottomWidth).toBe(StyleSheet.hairlineWidth);
      expect(typeof flat.borderBottomColor).toBe('string');
    });

    it('omits the bottom divider when isLast is true', () => {
      const { getByTestId } = render(
        <ListItemRow
          item={item}
          isLast
          onToggle={jest.fn()}
          onDelete={jest.fn()}
        />,
      );
      const row = getByTestId('list-item-row');
      const flat = Array.isArray(row.props.style)
        ? Object.assign({}, ...row.props.style.filter(Boolean))
        : row.props.style;
      expect(flat.borderBottomWidth ?? 0).toBe(0);
    });

    it('snapshot — three-item block (first / middle / last)', () => {
      const items = [
        { ...item, id: 'i-1', product_name: 'Pommes' },
        { ...item, id: 'i-2', product_name: 'Lait' },
        { ...item, id: 'i-3', product_name: 'Pain' },
      ];
      const tree = render(
        <>
          {items.map((it, i) => (
            <ListItemRow
              key={it.id}
              item={it}
              isFirst={i === 0}
              isLast={i === items.length - 1}
              onToggle={jest.fn()}
              onDelete={jest.fn()}
            />
          ))}
        </>,
      );
      expect(tree.toJSON()).toMatchSnapshot();
    });
  });
});
