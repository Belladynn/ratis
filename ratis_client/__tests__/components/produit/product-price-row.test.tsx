// __tests__/components/produit/product-price-row.test.tsx
//
// Restored at chunk 5 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Original V4 test (commit 932c065^) restored as-is — the V5 component keeps
// the same API contract (storeName/distanceKm/priceCents/isBest/deltaPct/isLast).

import React from 'react';
import { render } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  return {
    LinearGradient: ({ children, ...props }: { children?: React.ReactNode }) =>
      RnReact.createElement(RN.View, props, children),
  };
});

import { ProductPriceRow } from '@/components/produit/product-price-row';

type StyledNode = {
  props: { style?: Record<string, unknown> | Record<string, unknown>[] };
};

function styleOf(node: StyledNode): Record<string, unknown> {
  const s = node.props.style;
  if (Array.isArray(s)) {
    return Object.assign({}, ...s.filter(Boolean)) as Record<string, unknown>;
  }
  return (s ?? {}) as Record<string, unknown>;
}

describe('ProductPriceRow (V5 strict iso)', () => {
  it('renders store name, distance, price', () => {
    const { getByText } = render(
      <ProductPriceRow
        storeName="Leclerc Parmentier"
        distanceKm={1.2}
        priceCents={119}
        isBest={false}
      />,
    );
    expect(getByText('Leclerc Parmentier')).toBeTruthy();
    expect(getByText('1,2 km')).toBeTruthy();
    expect(getByText('1,19€')).toBeTruthy();
  });

  it('highlights price in gold when isBest', () => {
    const { getByTestId } = render(
      <ProductPriceRow
        storeName="Leclerc"
        distanceKm={1}
        priceCents={100}
        isBest={true}
      />,
    );
    const priceElement = getByTestId('price-val');
    expect(styleOf(priceElement).color).toBe('#FFB800');
  });

  it('renders MEILLEUR label and crown medallion when isBest', () => {
    const { getByText, getByTestId } = render(
      <ProductPriceRow
        storeName="Auchan Nation"
        distanceKm={2.8}
        priceCents={420}
        isBest
      />,
    );
    expect(getByText('MEILLEUR')).toBeTruthy();
    expect(getByTestId('best-medallion')).toBeTruthy();
    expect(getByText('👑')).toBeTruthy();
  });

  it('renders +X% delta when not best and deltaPct > 0', () => {
    const { getByText } = render(
      <ProductPriceRow
        storeName="Carrefour"
        distanceKm={1.2}
        priceCents={450}
        isBest={false}
        deltaPct={7.14}
      />,
    );
    // Math.round(7.14) → 7
    expect(getByText('+7%')).toBeTruthy();
  });

  it('does NOT render delta when deltaPct is 0 or undefined', () => {
    const { queryByText } = render(
      <ProductPriceRow
        storeName="Carrefour"
        distanceKm={1.2}
        priceCents={450}
        isBest={false}
      />,
    );
    expect(queryByText(/\+\d+%/)).toBeNull();
  });
});
