// ratis_client/__tests__/components/achievements/unlock-toast.test.tsx
//
// Achievements V1 — toast contract tests (PR 8/8).
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';

import { AchievementUnlockToast } from '@/components/achievements/unlock-toast';
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
  code: 'v_first',
  label: 'Premier scan',
  description: 'Scanner ton tout premier ticket',
  rarity: 'terracotta',
  category: 'volume',
  icon: '🎬',
  cab_granted: 20,
  show_modal: false,
  has_bespoke: false,
  sound_intensity: 1,
};

describe('AchievementUnlockToast', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('renders the achievement label, icon and rarity name', () => {
    const { getByText } = render(
      <AchievementUnlockToast payload={BASE} onDismiss={() => {}} />,
    );
    expect(getByText('Premier scan')).toBeTruthy();
    expect(getByText('🎬')).toBeTruthy();
    // Rarity label is rendered inside the eyebrow line.
    expect(getByText(/Terre cuite/i)).toBeTruthy();
  });

  it('returns null when payload is missing', () => {
    const { toJSON } = render(
      <AchievementUnlockToast payload={null} onDismiss={() => {}} />,
    );
    expect(toJSON()).toBeNull();
  });

  it('calls onDismiss after the visibility window (4500ms)', () => {
    const onDismiss = jest.fn();
    render(
      <AchievementUnlockToast payload={BASE} onDismiss={onDismiss} />,
    );
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(4500);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('dismisses immediately when tapped', () => {
    const onDismiss = jest.fn();
    const { getByTestId } = render(
      <AchievementUnlockToast
        payload={BASE}
        onDismiss={onDismiss}
        testID="toast"
      />,
    );
    fireEvent.press(getByTestId('toast'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('switches palette per rarity (sapphire eyebrow renders Saphir)', () => {
    const { getByText } = render(
      <AchievementUnlockToast
        payload={{ ...BASE, rarity: 'sapphire' }}
        onDismiss={() => {}}
      />,
    );
    expect(getByText(/Saphir/i)).toBeTruthy();
  });
});
