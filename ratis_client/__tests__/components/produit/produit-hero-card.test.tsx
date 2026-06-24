// __tests__/components/produit/produit-hero-card.test.tsx
//
// Restored at chunk 5 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Original V4 test (commit 932c065^) restored as-is — the V5 component keeps
// the same API contract (brand/name/ean/photoUrl/fallbackEmoji).

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

import { ProduitHeroCard } from '@/components/produit/produit-hero-card';

describe('ProduitHeroCard (V5 strict iso)', () => {
  it('renders brand uppercase, name, and EAN', () => {
    const { getByText } = render(
      <ProduitHeroCard
        brand="Nespresso"
        name="Capsules Café Vivalto Lungo x10"
        ean="7640110350683"
      />,
    );
    expect(getByText('NESPRESSO')).toBeTruthy();
    expect(getByText('Capsules Café Vivalto Lungo x10')).toBeTruthy();
    expect(getByText('7640110350683')).toBeTruthy();
  });

  it('omits brand line when brand is null', () => {
    const { queryByText, getByText } = render(
      <ProduitHeroCard
        brand={null}
        name="Lait demi-écrémé"
        ean="3428270000019"
      />,
    );
    expect(queryByText('NESPRESSO')).toBeNull();
    expect(getByText('Lait demi-écrémé')).toBeTruthy();
  });

  it('renders fallback emoji when no photo url', () => {
    const { getByText } = render(
      <ProduitHeroCard
        brand="Brand"
        name="Item"
        ean="1234567890123"
        fallbackEmoji="☕"
      />,
    );
    expect(getByText('☕')).toBeTruthy();
  });

  it('renders default emoji 📦 when fallbackEmoji is omitted', () => {
    const { getByText } = render(
      <ProduitHeroCard brand={null} name="Item" ean="1234567890123" />,
    );
    expect(getByText('📦')).toBeTruthy();
  });
});
