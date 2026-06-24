// __tests__/components/produit/produit-info-table.test.tsx
//
// Restored at chunk 5 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// Original V4 test (commit 932c065^) restored as-is — the V5 component keeps
// the same API contract (rows array of `{key, value}`).

import React from 'react';
import { render } from '@testing-library/react-native';

import { ProduitInfoTable } from '@/components/produit/produit-info-table';

describe('ProduitInfoTable (V5 strict iso)', () => {
  it('renders the section header from i18n', () => {
    const { getByText } = render(<ProduitInfoTable rows={[]} />);
    expect(getByText(/CARACTÉRISTIQUES/i)).toBeTruthy();
  });

  it('renders all key/value rows in order', () => {
    const rows = [
      { key: 'Quantité', value: '10 capsules' },
      { key: 'Marque', value: 'Nespresso' },
      { key: 'Origine', value: 'Suisse' },
    ];
    const { getByText } = render(<ProduitInfoTable rows={rows} />);
    expect(getByText('Quantité')).toBeTruthy();
    expect(getByText('10 capsules')).toBeTruthy();
    expect(getByText('Marque')).toBeTruthy();
    expect(getByText('Nespresso')).toBeTruthy();
    expect(getByText('Origine')).toBeTruthy();
    expect(getByText('Suisse')).toBeTruthy();
  });

  it('renders an empty table when rows is empty', () => {
    const { queryByText } = render(<ProduitInfoTable rows={[]} />);
    // Header still present
    expect(queryByText(/CARACTÉRISTIQUES/i)).toBeTruthy();
  });
});
