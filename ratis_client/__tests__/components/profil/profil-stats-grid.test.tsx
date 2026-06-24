// __tests__/components/profil/profil-stats-grid.test.tsx
//
// Restored in chunk 6 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// The stats grid was rebuilt iso `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 595-600 — see `components/profil/profil-stats-grid.tsx`. The
// original test in commit 01d62ff already targeted the V5 contract, so it
// is restored verbatim.

import React from 'react';
import { render } from '@testing-library/react-native';
import { ProfilStatsGrid } from '@/components/profil/profil-stats-grid';

describe('ProfilStatsGrid (V5)', () => {
  it('renders three stat tiles with values and uppercase labels', () => {
    const { getByText, getByTestId } = render(
      <ProfilStatsGrid cabBalance={12480} scanCount={47} savingsEuros={48} />,
    );
    // CAB value uses NBSP-separated thousands
    expect(getByText('12 480')).toBeTruthy();
    expect(getByText('47')).toBeTruthy();
    expect(getByText('48€')).toBeTruthy();
    // Uppercase labels per V5 design
    expect(getByText('CAB')).toBeTruthy();
    expect(getByText('SCANS')).toBeTruthy();
    expect(getByText('ÉCONOMIES')).toBeTruthy();
    // testIDs for each tile
    expect(getByTestId('profil-stat-cab')).toBeTruthy();
    expect(getByTestId('profil-stat-scans')).toBeTruthy();
    expect(getByTestId('profil-stat-savings')).toBeTruthy();
  });
});
