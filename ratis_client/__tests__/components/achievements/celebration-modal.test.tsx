// ratis_client/__tests__/components/achievements/celebration-modal.test.tsx
//
// Achievements V1 — celebration modal contract tests (PR 8/8).
import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';

import { AchievementCelebrationModal } from '@/components/achievements/celebration-modal';
import type { AchievementUnlockedPayload } from '@/types/achievements';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

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
      children?: React.ReactNode;
    }) =>
      ReactMock.createElement(
        RN.View,
        props,
        children as React.ReactNode,
      ),
  };
});

const BASE: AchievementUnlockedPayload = {
  notif_type: 'achievement_unlocked',
  achievement_id: 'aaaa-1111',
  code: 'r_30',
  label: 'Mois sans rater',
  description: 'Streak de 30 jours',
  rarity: 'sapphire',
  category: 'streak',
  icon: '🔥',
  cab_granted: 250,
  show_modal: true,
  has_bespoke: false,
  sound_intensity: 2,
};

describe('AchievementCelebrationModal', () => {
  it('does not render when payload is null', () => {
    const { toJSON } = render(
      <AchievementCelebrationModal payload={null} onDismiss={() => {}} />,
    );
    expect(toJSON()).toBeNull();
  });

  it('renders for emerald+ rarities', () => {
    const { getByText } = render(
      <AchievementCelebrationModal payload={BASE} onDismiss={() => {}} />,
    );
    expect(getByText('Mois sans rater')).toBeTruthy();
    expect(getByText('Streak de 30 jours')).toBeTruthy();
    expect(getByText('🔥')).toBeTruthy();
    expect(getByText(/\+250 CAB/)).toBeTruthy();
  });

  it('does NOT render for terracotta / bronze / copper / silver / gold', () => {
    for (const rarity of ['terracotta', 'bronze', 'copper', 'silver', 'gold'] as const) {
      const { toJSON } = render(
        <AchievementCelebrationModal
          payload={{ ...BASE, rarity }}
          onDismiss={() => {}}
        />,
      );
      expect(toJSON()).toBeNull();
    }
  });

  it('renders for emerald, sapphire, ruby, crystal, diamond', () => {
    for (const rarity of ['emerald', 'sapphire', 'ruby', 'crystal', 'diamond'] as const) {
      const { getByText } = render(
        <AchievementCelebrationModal
          payload={{ ...BASE, rarity }}
          onDismiss={() => {}}
        />,
      );
      expect(getByText('Mois sans rater')).toBeTruthy();
    }
  });

  it('calls onDismiss when the close button is pressed', () => {
    const onDismiss = jest.fn();
    const { getByTestId } = render(
      <AchievementCelebrationModal payload={BASE} onDismiss={onDismiss} />,
    );
    fireEvent.press(getByTestId('achievement-celebration-close'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('calls onDismiss when the backdrop is pressed', () => {
    const onDismiss = jest.fn();
    const { getByTestId } = render(
      <AchievementCelebrationModal payload={BASE} onDismiss={onDismiss} />,
    );
    fireEvent.press(getByTestId('achievement-celebration-backdrop'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
