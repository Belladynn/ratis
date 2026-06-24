// V5 battlepass-card — adapted from git@01d62ff. The V5 tile testIDs are
// indexed 0-4 by tile slot (NOT by absolute level), and the skeleton testID
// follows the canonical `<root>-skeleton` pattern.
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

import { BattlepassCard } from '@/components/dashboard/battlepass-card';
import type { BattlepassState } from '@/types/gamification';

const BP: BattlepassState = {
  season_name: 'Printemps 2026',
  current_level: 14,
  xp_current: 340,
  xp_next_level: 580,
  next_reward_label: 'Badge Chasseur',
  next_reward_type: 'skin',
};

describe('BattlepassCard', () => {
  it('renders season name and level', () => {
    const { getByText } = render(
      <BattlepassCard battlepass={BP} isLoading={false} onPress={jest.fn()} />,
    );
    expect(getByText(/Printemps 2026/)).toBeTruthy();
    expect(getByText(/Niv\.\s*14/)).toBeTruthy();
  });

  it('renders 5 tier tiles (0..4 slot indices, current-1 → current+3)', () => {
    const { getByTestId } = render(
      <BattlepassCard battlepass={BP} isLoading={false} onPress={jest.fn()} />,
    );
    for (let i = 0; i < 5; i++) {
      expect(getByTestId(`battlepass-card-tile-${i}`)).toBeTruthy();
    }
  });

  it('shows skeleton when loading or null', () => {
    const { getByTestId } = render(
      <BattlepassCard battlepass={null} isLoading={true} onPress={jest.fn()} />,
    );
    expect(getByTestId('battlepass-card-skeleton')).toBeTruthy();
  });

  it('calls onPress when pressed', () => {
    const onPress = jest.fn();
    const { getByTestId } = render(
      <BattlepassCard battlepass={BP} isLoading={false} onPress={onPress} />,
    );
    fireEvent.press(getByTestId('battlepass-card'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  // Bug 5 (PO ticket 2026-05-12 wave 2) — for a fresh user, `current_level`
  // is 0 (no milestone claimed yet). The pre-fix code displayed "Niv. -1"
  // on the first tile slot because `startLevel = current_level - 1`.
  describe('Bug 5 — fresh user (current_level = 0)', () => {
    const FRESH: BattlepassState = {
      season_name: 'Solo',
      current_level: 0,
      xp_current: 0,
      xp_next_level: 200,
      next_reward_label: '+100 CAB',
      next_reward_type: 'cab',
    };

    it('displays "Niv. 0" in the header (never negative)', () => {
      const { getByText, queryByText } = render(
        <BattlepassCard battlepass={FRESH} isLoading={false} />,
      );
      expect(getByText(/Niv\.\s*0/)).toBeTruthy();
      expect(queryByText(/Niv\.\s*-1/)).toBeNull();
    });

    it('renders 5 tier tiles starting at level 0 (no -1 slot)', () => {
      const { getByTestId, queryByText } = render(
        <BattlepassCard battlepass={FRESH} isLoading={false} />,
      );
      for (let i = 0; i < 5; i++) {
        expect(getByTestId(`battlepass-card-tile-${i}`)).toBeTruthy();
      }
      // No tile should display the level "-1".
      expect(queryByText('-1')).toBeNull();
    });

    it('next reward pill reads "Niv. 1" (current + 1)', () => {
      const { getAllByText, queryByText } = render(
        <BattlepassCard battlepass={FRESH} isLoading={false} />,
      );
      // Regex matches both « Niv. 1 » (next reward pill + xp delta line).
      const nivOnes = getAllByText(/Niv\.\s*1\b/);
      expect(nivOnes.length).toBeGreaterThanOrEqual(1);
      // Defensive — no negative slot.
      expect(queryByText(/Niv\.\s*-1/)).toBeNull();
    });
  });

  // Bug 5 defensive — negative inputs from upstream are clamped.
  it('clamps negative current_level to 0 (defensive)', () => {
    const NEG: BattlepassState = {
      season_name: 'Buggy',
      current_level: -3,
      xp_current: 0,
      xp_next_level: 100,
      next_reward_label: '',
      next_reward_type: null,
    };
    const { getByText, queryByText } = render(
      <BattlepassCard battlepass={NEG} isLoading={false} />,
    );
    expect(getByText(/Niv\.\s*0/)).toBeTruthy();
    expect(queryByText(/Niv\.\s*-3/)).toBeNull();
  });
});
