// __tests__/components/produit/product-consensus-card.test.tsx
//
// Restored at chunk 5 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Adapted from the V4 test (commit 932c065^) — the V5 component keeps the same
// API contract (priceCents/storesCount/radiusKm/locationDenied). i18n strings
// live under the new `produit.*` namespace.

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

jest.mock('react-native-svg', () => {
  const RN = require('react-native');
  const RnReact = require('react');
  const Pass = ({
    children,
    ...props
  }: {
    children?: React.ReactNode;
    [k: string]: unknown;
  }) => RnReact.createElement(RN.View, props, children);
  return {
    __esModule: true,
    default: Pass,
    Svg: Pass,
    Path: () => null,
    Circle: () => null,
    Rect: () => null,
  };
});

import { ProductConsensusCard } from '@/components/produit/product-consensus-card';

describe('ProductConsensusCard (V5 strict iso)', () => {
  it('renders price formatted when priceCents provided', () => {
    const { getByText } = render(
      <ProductConsensusCard priceCents={119} storesCount={4} />,
    );
    expect(getByText('1,19€')).toBeTruthy();
  });

  it('renders dash when priceCents is null', () => {
    const { getByText } = render(
      <ProductConsensusCard priceCents={null} storesCount={0} />,
    );
    expect(getByText('—')).toBeTruthy();
  });

  it('renders stores count sub (no radius) when storesCount > 0', () => {
    const { getByText } = render(
      <ProductConsensusCard priceCents={119} storesCount={4} />,
    );
    expect(getByText(/4 magasins à proximité/)).toBeTruthy();
  });

  it('renders V5 "{n} magasins · {km} km autour" when radiusKm is provided', () => {
    const { getByText } = render(
      <ProductConsensusCard priceCents={119} storesCount={7} radiusKm={4} />,
    );
    expect(getByText(/7 magasins · 4 km autour/)).toBeTruthy();
  });

  it('renders empty sub when storesCount is 0 and no locationDenied flag', () => {
    const { getByText } = render(
      <ProductConsensusCard priceCents={null} storesCount={0} />,
    );
    expect(getByText(/Aucun prix disponible/)).toBeTruthy();
  });

  it('renders location hint when locationDenied is true and no stores', () => {
    const { getByText } = render(
      <ProductConsensusCard
        priceCents={null}
        storesCount={0}
        locationDenied
      />,
    );
    expect(getByText(/Active la géoloc pour voir les prix/)).toBeTruthy();
  });

  it('renders "MEILLEUR PRIX" label (V5 strict iso)', () => {
    const { getByText } = render(
      <ProductConsensusCard priceCents={119} storesCount={4} />,
    );
    expect(getByText('MEILLEUR PRIX')).toBeTruthy();
  });
});
