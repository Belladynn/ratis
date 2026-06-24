// __tests__/components/profil/profil-menu-row.test.tsx
//
// Restored in chunk 6 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// The menu row was rebuilt iso `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 507-543 — see `components/profil/profil-menu-row.tsx`. The
// original test in commit 01d62ff already targeted the V5 contract, so it
// is restored verbatim.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { ProfilMenuRow } from '@/components/profil/profil-menu-row';

describe('ProfilMenuRow (V5)', () => {
  it('renders title and subtitle', () => {
    const { getByText } = render(
      <ProfilMenuRow
        icon="🎁"
        iconColor="gold"
        title="Boutique"
        subtitle="Cartes cadeaux · bonus"
        onPress={jest.fn()}
      />,
    );
    expect(getByText('Boutique')).toBeTruthy();
    expect(getByText('Cartes cadeaux · bonus')).toBeTruthy();
  });

  it('calls onPress when tapped (enabled row)', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <ProfilMenuRow
        icon="🎁"
        iconColor="gold"
        title="Boutique"
        onPress={onPress}
      />,
    );
    fireEvent.press(getByTestId('profil-menu-row'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not invoke onPress when disabled', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <ProfilMenuRow
        icon="🔔"
        iconColor="gold"
        title="Notifications"
        onPress={onPress}
        disabled
      />,
    );
    fireEvent.press(getByTestId('profil-menu-row'));
    expect(onPress).not.toHaveBeenCalled();
  });

  it('renders danger variant without chevron', () => {
    const { queryByText, getByText } = render(
      <ProfilMenuRow
        icon="🚪"
        iconColor="red"
        title="Se déconnecter"
        subtitle="Tu seras redirigé"
        onPress={jest.fn()}
        danger
      />,
    );
    expect(getByText('Se déconnecter')).toBeTruthy();
    expect(queryByText('›')).toBeNull();
  });
});
