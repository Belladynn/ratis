// __tests__/components/profil/profil-menu-group.test.tsx
//
// Restored in chunk 6 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// The menu group was rebuilt iso `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 545-559 — see `components/profil/profil-menu-group.tsx`. The
// original test in commit 01d62ff already targeted the V5 contract, so it
// is restored verbatim.

import React from 'react';
import { render } from '@testing-library/react-native';
import { Text } from 'react-native';
import { ProfilMenuGroup } from '@/components/profil/profil-menu-group';

describe('ProfilMenuGroup (V5)', () => {
  it('renders uppercase label and children', () => {
    const { getByText } = render(
      <ProfilMenuGroup label="Récompenses" accent="rewards">
        <Text>child row</Text>
      </ProfilMenuGroup>,
    );
    expect(getByText('RÉCOMPENSES')).toBeTruthy();
    expect(getByText('child row')).toBeTruthy();
  });

  it('supports danger accent for the SESSION group', () => {
    const { getByText } = render(
      <ProfilMenuGroup label="Session" accent="danger" testID="grp-session">
        <Text>logout row</Text>
      </ProfilMenuGroup>,
    );
    expect(getByText('SESSION')).toBeTruthy();
    expect(getByText('logout row')).toBeTruthy();
  });
});
