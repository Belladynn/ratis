// __tests__/components/dashboard/achievement-card.test.tsx
//
// Restored in chunk 7 of visual iso V5 reconstruction. The original test
// in commit 01d62ff targeted a deleted V4 component (`@/data/achievements`
// data module that was wiped) — rewritten here against the V5 component
// in `components/profil/achievement-card.tsx`.
//
// Note : the V5 component lives under `components/profil/` (not
// `components/dashboard/`) — this test file is kept under
// `__tests__/components/dashboard/` to preserve the original chunk-1
// skip placeholder location. The pointer to the V5 component is the
// import path.

import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { AchievementCard } from '@/components/profil/achievement-card';
import type { Achievement } from '@/components/profil/achievements-data';

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

const mkUnlocked = (): Achievement => ({
  id: 'v_first',
  label: 'Premier scan',
  description: 'Scanner ton tout premier ticket',
  icon: '🎬',
  rarity: 'terracotta',
  category: 'volume',
  progress: 1,
  target: 1,
  status: 'unlocked',
});

const mkInProgress = (): Achievement => ({
  id: 'v_50',
  label: 'Cinquantaine',
  description: 'Scanner 50 tickets',
  icon: '📑',
  rarity: 'copper',
  category: 'volume',
  progress: 47,
  target: 50,
  status: 'in_progress',
});

const mkLockedSecret = (): Achievement => ({
  id: 'sec_konami',
  label: '???',
  description: 'Succès secret',
  icon: '❓',
  rarity: 'diamond',
  category: 'secret',
  progress: 0,
  target: 1,
  status: 'locked',
});

const mkLegendary = (): Achievement => ({
  id: 'r_365',
  label: 'Une année',
  description: 'Streak de 365 jours',
  icon: '🌌',
  rarity: 'diamond',
  category: 'streak',
  progress: 365,
  target: 365,
  status: 'unlocked',
});

describe('AchievementCard (V5)', () => {
  it('renders the achievement label when unlocked', () => {
    const { getByText } = render(<AchievementCard achievement={mkUnlocked()} />);
    expect(getByText('Premier scan')).toBeTruthy();
  });

  it('renders the rarity label in the ribbon', () => {
    const { getByText } = render(<AchievementCard achievement={mkUnlocked()} />);
    expect(getByText('Terre cuite')).toBeTruthy();
  });

  it('shows progress for low-tier in_progress achievements', () => {
    const { getByText } = render(
      <AchievementCard achievement={mkInProgress()} />,
    );
    expect(getByText('47/50')).toBeTruthy();
  });

  it('renders "???" instead of the label for secret locked tiles', () => {
    const { getByText } = render(
      <AchievementCard achievement={mkLockedSecret()} />,
    );
    expect(getByText('???')).toBeTruthy();
  });

  it('still mounts unlocked legendary tiles (burst rays animation)', () => {
    const { getByTestId } = render(
      <AchievementCard achievement={mkLegendary()} testID="ach-legendary" />,
    );
    expect(getByTestId('ach-legendary')).toBeTruthy();
  });

  it('calls onPress with the achievement when tapped', () => {
    const onPress = jest.fn();
    const ach = mkUnlocked();
    const { getByTestId } = render(
      <AchievementCard achievement={ach} onPress={onPress} testID="ach-tap" />,
    );
    fireEvent.press(getByTestId('ach-tap'));
    expect(onPress).toHaveBeenCalledWith(ach);
  });

  // Bug 4 (PO ticket 2026-05-12 wave 2) — tier colours (terre cuite,
  // bronze, cuivre, argent, or, …) must be visible on EVERY card, not
  // just on unlocked ones. We verify it by reading the ribbon's tier
  // label, which the pre-fix UI rendered at 40% white opacity (so its
  // *colour* hex was unreadable). The post-fix UI keeps the tier label
  // visible on locked tiles too.
  describe('Bug 4 — tier colours on locked achievements', () => {
    const mkLockedBronze = (): Achievement => ({
      id: 'soc_invite_1',
      label: 'Recruteur',
      description: 'Inviter 1 ami',
      icon: '🤝',
      rarity: 'bronze',
      category: 'social',
      progress: 0,
      target: 1,
      status: 'locked',
    });

    const mkLockedCopper = (): Achievement => ({
      id: 'r_7',
      label: 'Semaine pleine',
      description: 'Streak de 7 jours',
      icon: '🔥',
      rarity: 'copper',
      category: 'streak',
      progress: 0,
      target: 7,
      status: 'locked',
    });

    it.each([
      ['bronze', 'Bronze', mkLockedBronze],
      ['copper', 'Cuivre', mkLockedCopper],
      ['terracotta', 'Terre cuite', mkUnlocked],
    ] as const)(
      'rarity %s — tier label "%s" is rendered on the ribbon',
      (_rarity, expectedLabel, mk) => {
        const { getByText } = render(<AchievementCard achievement={mk()} />);
        expect(getByText(expectedLabel)).toBeTruthy();
      },
    );

    it('renders the tier ribbon even when the achievement is locked', () => {
      const { getByText } = render(
        <AchievementCard achievement={mkLockedBronze()} />,
      );
      // The bronze tier label must read on a locked card too (the pre-fix
      // UI showed it at 40% opacity, but the text was still in the tree).
      expect(getByText('Bronze')).toBeTruthy();
    });
  });
});
