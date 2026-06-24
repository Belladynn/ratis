// V5 jack-streak-button — fresh test (V4 had a `JackCard` with a different
// shape ; V5's `JackStreakButton` is the right-column hero CTA).
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { JackStreakButton } from '@/components/dashboard/jack-streak-button';
import type { StreakState } from '@/types/gamification';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children, style }: any) => {
    const { View } = require('react-native');
    return <View style={style}>{children}</View>;
  },
}));

const FED: StreakState = {
  streak_days: 7,
  multiplier: 0.35,
  food_reserves: 2,
  already_fed_today: true,
  needs_repair: false,
  last_fed_at: '2026-04-20',
};

const HUNGRY: StreakState = {
  streak_days: 3,
  multiplier: 0.15,
  food_reserves: 0,
  already_fed_today: false,
  needs_repair: false,
  last_fed_at: '2026-04-19',
};

describe('JackStreakButton', () => {
  it('shows skeleton when isLoading', () => {
    const { getByTestId } = render(
      <JackStreakButton streak={null} isLoading={true} />,
    );
    expect(getByTestId('jack-streak-button-skeleton')).toBeTruthy();
  });

  it('shows the day count', () => {
    const { getByText } = render(
      <JackStreakButton streak={FED} isLoading={false} />,
    );
    expect(getByText('7')).toBeTruthy();
  });

  it('renders the fed state when already_fed_today = true', () => {
    const { getByText, getByTestId } = render(
      <JackStreakButton streak={FED} isLoading={false} />,
    );
    expect(getByText(/Rassasié/)).toBeTruthy();
    expect(getByTestId('jack-streak-fed-cta')).toBeTruthy();
  });

  it('renders the hungry CTA + bonus pill when not fed', () => {
    const { getByText, getByTestId } = render(
      <JackStreakButton streak={HUNGRY} isLoading={false} />,
    );
    expect(getByText(/Nourrir Jack/)).toBeTruthy();
    expect(getByTestId('jack-streak-feed-cta')).toBeTruthy();
    // multiplier 0.15 → +15%
    expect(getByText(/\+15%/)).toBeTruthy();
  });

  it('calls onFeed when the hungry CTA is pressed', () => {
    const onFeed = jest.fn();
    const { getByTestId } = render(
      <JackStreakButton streak={HUNGRY} isLoading={false} onFeed={onFeed} />,
    );
    fireEvent.press(getByTestId('jack-streak-feed-cta'));
    expect(onFeed).toHaveBeenCalledTimes(1);
  });

  it('does not call onFeed when fed (CTA disabled)', () => {
    const onFeed = jest.fn();
    const { getByTestId } = render(
      <JackStreakButton streak={FED} isLoading={false} onFeed={onFeed} />,
    );
    fireEvent.press(getByTestId('jack-streak-fed-cta'));
    expect(onFeed).not.toHaveBeenCalled();
  });

  // Bug 2 (PO ticket 2026-05-12 wave 3) — the right-column CTA must read
  // as a button when hungry. PR #427 wired `onFeed` but the day-count
  // pressable still showed "JOURS" (passive label). PO still couldn't
  // perceive a button. Fix : the hungry CTA now reads "NOURRIR" and the
  // fed CTA falls back to "JOURS".
  describe('Bug 2 wave 3 — CTA label reads as a button when hungry', () => {
    it('renders the hungry CTA with the "NOURRIR" label (not "JOURS")', () => {
      const { getByText, queryByText } = render(
        <JackStreakButton streak={HUNGRY} isLoading={false} />,
      );
      expect(getByText('NOURRIR')).toBeTruthy();
      // Confirm the passive "JOURS" label is NOT shown in the hungry
      // state (the regression : pre-fix UI showed "JOURS" both states).
      expect(queryByText('JOURS')).toBeNull();
    });

    it('renders the fed CTA with the "JOURS" label (no action available)', () => {
      const { getByText, queryByText } = render(
        <JackStreakButton streak={FED} isLoading={false} />,
      );
      expect(getByText('JOURS')).toBeTruthy();
      expect(queryByText('NOURRIR')).toBeNull();
    });

    // The feed-cta testID MUST resolve to a Pressable with onFeed wired.
    // This catches the regression where the button was hidden by a
    // condition (e.g. streak_days === 0 OR food_reserves === 0). It must
    // render unconditionally as long as the user isn't fed today.
    it.each([
      [0, 0, false],
      [1, 0, false],
      [7, 2, false],
      [365, 99, false],
    ])(
      'renders the feed CTA when not fed (streak=%i, reserves=%i, fed=%s)',
      (streak_days, food_reserves, already_fed_today) => {
        const onFeed = jest.fn();
        const { getByTestId } = render(
          <JackStreakButton
            streak={{
              streak_days,
              multiplier: 0,
              food_reserves,
              already_fed_today,
              needs_repair: false,
              last_fed_at: null,
            }}
            isLoading={false}
            onFeed={onFeed}
          />,
        );
        // The CTA must be queryable AND pressable in every hungry state.
        const cta = getByTestId('jack-streak-feed-cta');
        expect(cta).toBeTruthy();
        fireEvent.press(cta);
        expect(onFeed).toHaveBeenCalledTimes(1);
      },
    );
  });
});
