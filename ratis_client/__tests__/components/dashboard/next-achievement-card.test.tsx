// V5 next-achievement-card — fresh test (V4 version depended on the deleted
// `@/data/achievements` module + V4-specific testIDs `next-achievement-*`).
// V1 ships a UI-only component that consumes a single (already-picked)
// achievement object plus an exported `pickNextAchievement` helper.
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children, style, testID }: any) => {
    const { View } = require('react-native');
    return <View testID={testID} style={style}>{children}</View>;
  },
}));

import {
  NextAchievementCard,
  pickNextAchievement,
  type NextAchievement,
} from '@/components/dashboard/next-achievement-card';

const mk = (
  id: string,
  status: NextAchievement['status'],
  rarity: NextAchievement['rarity'],
  progress: number,
  target: number,
  label = 'Test',
): NextAchievement => ({ id, label, rarity, progress, target, status });

describe('pickNextAchievement', () => {
  it('returns null when no in_progress entry exists', () => {
    expect(
      pickNextAchievement([
        mk('a', 'unlocked', 'common', 10, 10),
        mk('b', 'locked', 'legendary', 0, 100),
      ]),
    ).toBeNull();
  });

  it('returns the in_progress achievement closest to completion', () => {
    const top = mk('top', 'in_progress', 'rare', 47, 50, 'Demi-bil'); // 94%
    const mid = mk('mid', 'in_progress', 'epic', 47, 100, 'Centurion'); // 47%
    const low = mk('low', 'in_progress', 'legendary', 47, 500); // 9.4%
    expect(pickNextAchievement([low, mid, top])?.id).toBe('top');
  });

  it('does not mutate the input list', () => {
    const items: NextAchievement[] = [
      mk('a', 'in_progress', 'epic', 10, 100),
      mk('b', 'in_progress', 'rare', 47, 50),
    ];
    const before = items.map((a) => a.id);
    pickNextAchievement(items);
    expect(items.map((a) => a.id)).toEqual(before);
  });
});

describe('NextAchievementCard', () => {
  it('renders nothing when achievement is null/undefined', () => {
    const { toJSON } = render(<NextAchievementCard achievement={null} />);
    expect(toJSON()).toBeNull();
  });

  it('renders the achievement label', () => {
    const a = mk('demi', 'in_progress', 'rare', 47, 50, 'Demi-bilan');
    const { getByText } = render(<NextAchievementCard achievement={a} />);
    expect(getByText('Demi-bilan')).toBeTruthy();
    expect(getByText(/47 \/ 50/)).toBeTruthy();
  });

  it('calls onPress when tapped', () => {
    const a = mk('demi', 'in_progress', 'rare', 47, 50);
    const onPress = jest.fn();
    const { getByTestId } = render(
      <NextAchievementCard achievement={a} onPress={onPress} testID="nac" />,
    );
    fireEvent.press(getByTestId('nac'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });
});
