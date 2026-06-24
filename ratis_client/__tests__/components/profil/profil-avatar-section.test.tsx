// __tests__/components/profil/profil-avatar-section.test.tsx
//
// Restored in chunk 6 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// The avatar section was rebuilt iso `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 570-593 — see `components/profil/profil-avatar-section.tsx`. The
// original test in commit 01d62ff already targeted the V5 contract, so it
// is restored verbatim.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { ProfilAvatarSection } from '@/components/profil/profil-avatar-section';

jest.mock('expo-linear-gradient', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const RN = require('react-native');
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const ReactMock = require('react');
  return {
    LinearGradient: ({
      children,
      ...props
    }: {
      children?: unknown;
    }) => ReactMock.createElement(RN.View, props, children),
  };
});

describe('ProfilAvatarSection (V5)', () => {
  it('renders name, handle and level badge', () => {
    const { getByText, getByTestId } = render(
      <ProfilAvatarSection name="Marie L." handle="@marie.l" level={12} />,
    );
    expect(getByText('Marie L.')).toBeTruthy();
    expect(getByText('@marie.l')).toBeTruthy();
    expect(getByText('★ Niv. 12')).toBeTruthy();
    expect(getByTestId('profil-level-badge')).toBeTruthy();
    expect(getByTestId('profil-avatar-section')).toBeTruthy();
  });

  it('invokes onPressAvatar when avatar is tapped', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <ProfilAvatarSection
        name="Alice"
        handle="@alice"
        level={3}
        onPressAvatar={onPress}
      />,
    );
    fireEvent.press(getByTestId('profil-avatar-press'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('uses default rat emoji when avatarEmoji is omitted', () => {
    const { getByText } = render(
      <ProfilAvatarSection name="Marie L." handle="@marie.l" level={1} />,
    );
    expect(getByText('🐀')).toBeTruthy();
  });
});
