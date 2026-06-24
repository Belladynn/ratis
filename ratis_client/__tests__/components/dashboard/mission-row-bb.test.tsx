// ratis_client/__tests__/components/dashboard/mission-row-bb.test.tsx
//
// Buffer + Burst (refonte 2026-05-09) — extended row component tests.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

import { MissionRowBB } from '@/components/dashboard/mission-row-bb';
import type { DailyMission } from '@/types/gamification';

const baseMission = (overrides: Partial<DailyMission> = {}): DailyMission => ({
  id: 'm1',
  action_type: 'receipt_scan',
  difficulty: 'easy',
  target_count: 3,
  current_count: 0,
  cab_reward: 100,
  xp_reward: 10,
  status: 'pending',
  frequency: 'daily',
  is_boostable: true,
  buffer_count: 0,
  burst_count: 0,
  burst_locked: false,
  portions_claimed: 0,
  ...overrides,
});

// Bug 2 (wave 4 — PO ticket 2026-05-12) — the Buffer button is now
// hidden behind ``bufferEnabled`` (defaults to false) so the row no
// longer surfaces it for the alpha. The original Buffer-button tests
// are preserved as ``MissionRowBB — Buffer button (bufferEnabled=true)``
// to lock the behaviour when the feature flips back on for V1.5.
describe('MissionRowBB — Buffer button (bufferEnabled=true)', () => {
  it('shows the Buffer button when all conditions hold', () => {
    const onBufferPress = jest.fn();
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission()}
        variant="daily"
        bufferEnabled
        onBufferPress={onBufferPress}
      />,
    );
    expect(getByTestId('mission-row-m1-buffer-btn')).toBeTruthy();
  });

  it('hides the Buffer button on weekly missions', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({ frequency: 'weekly' })}
        variant="weekly"
        bufferEnabled
      />,
    );
    expect(queryByTestId('mission-row-m1-buffer-btn')).toBeNull();
  });

  it('hides the Buffer button when buffer_count >= 3', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({ buffer_count: 3 })}
        variant="daily"
        bufferEnabled
      />,
    );
    expect(queryByTestId('mission-row-m1-buffer-btn')).toBeNull();
  });

  it('hides the Buffer button when burst_locked is true', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({ burst_locked: true })}
        variant="daily"
        bufferEnabled
      />,
    );
    expect(queryByTestId('mission-row-m1-buffer-btn')).toBeNull();
  });

  it('hides the Buffer button when is_boostable is false', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({ is_boostable: false })}
        variant="daily"
        bufferEnabled
      />,
    );
    expect(queryByTestId('mission-row-m1-buffer-btn')).toBeNull();
  });

  it('calls onBufferPress with the mission when pressed', () => {
    const onBufferPress = jest.fn();
    const m = baseMission();
    const { getByTestId } = render(
      <MissionRowBB
        mission={m}
        variant="daily"
        bufferEnabled
        onBufferPress={onBufferPress}
      />,
    );
    fireEvent.press(getByTestId('mission-row-m1-buffer-btn'));
    expect(onBufferPress).toHaveBeenCalledWith(m);
  });
});

// Bug 2 (wave 4 — PO ticket 2026-05-12) — default-off Buffer button.
describe('MissionRowBB — Buffer button hidden by default (Bug 2 wave 4)', () => {
  it('does NOT render the Buffer button when bufferEnabled is omitted', () => {
    const { queryByTestId } = render(
      <MissionRowBB mission={baseMission()} variant="daily" />,
    );
    expect(queryByTestId('mission-row-m1-buffer-btn')).toBeNull();
  });

  it('does NOT render the Buffer button when bufferEnabled=false', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission()}
        variant="daily"
        bufferEnabled={false}
      />,
    );
    expect(queryByTestId('mission-row-m1-buffer-btn')).toBeNull();
  });
});

describe('MissionRowBB — Buffer badge', () => {
  it('renders the Buffer × N badge when buffer_count > 0', () => {
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ buffer_count: 2 })}
        variant="daily"
      />,
    );
    expect(getByTestId('mission-row-m1-buffer-badge')).toBeTruthy();
  });

  it('does not render the badge when buffer_count = 0', () => {
    const { queryByTestId } = render(
      <MissionRowBB mission={baseMission()} variant="daily" />,
    );
    expect(queryByTestId('mission-row-m1-buffer-badge')).toBeNull();
  });
});

describe('MissionRowBB — Burst overlay', () => {
  it('hides the overlay before reaching the target', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({ current_count: 1, target_count: 3 })}
        variant="daily"
      />,
    );
    expect(queryByTestId('mission-row-m1-burst-overlay')).toBeNull();
  });

  it('shows the overlay once current_count >= target_count', () => {
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ current_count: 3, target_count: 3 })}
        variant="daily"
      />,
    );
    expect(getByTestId('mission-row-m1-burst-overlay')).toBeTruthy();
  });

  it('shows the Burst-claim button when at least one palier is unclaimed', () => {
    // current = 6 = target × 2 → palier 1 unlocked, burst_count=0 → 1 unclaimed
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({
          current_count: 6,
          target_count: 3,
          burst_count: 0,
        })}
        variant="daily"
      />,
    );
    expect(getByTestId('mission-row-m1-burst-claim-btn')).toBeTruthy();
  });

  it('hides the Burst-claim button when all reached paliers are already claimed', () => {
    // current = 6 = target × 2 → palier 1 reached, burst_count=1 → 0 unclaimed
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({
          current_count: 6,
          target_count: 3,
          burst_count: 1,
        })}
        variant="daily"
      />,
    );
    expect(queryByTestId('mission-row-m1-burst-claim-btn')).toBeNull();
  });

  it('calls onBurstClaim with the mission id when pressed', () => {
    const onBurstClaim = jest.fn();
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({
          current_count: 6,
          target_count: 3,
          burst_count: 0,
        })}
        variant="daily"
        onBurstClaim={onBurstClaim}
      />,
    );
    fireEvent.press(getByTestId('mission-row-m1-burst-claim-btn'));
    expect(onBurstClaim).toHaveBeenCalledWith('m1');
  });
});

describe('MissionRowBB — claim CTA', () => {
  it('disables the claim button when status is claimed', () => {
    const onClaim = jest.fn();
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ status: 'claimed' })}
        variant="daily"
        onClaim={onClaim}
      />,
    );
    fireEvent.press(getByTestId('mission-claim-m1'));
    expect(onClaim).not.toHaveBeenCalled();
  });

  // Bug 2 (PO ticket 2026-05-12 wave 2) — only `completed` missions are
  // claimable. `pending` / `active` rows display the muted CTA but the
  // press does nothing (the row is « pas encore prête »).
  it('disables the claim button when status is pending', () => {
    const onClaim = jest.fn();
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ status: 'pending' })}
        variant="daily"
        onClaim={onClaim}
      />,
    );
    fireEvent.press(getByTestId('mission-claim-m1'));
    expect(onClaim).not.toHaveBeenCalled();
  });

  it('calls onClaim with the mission id when status is completed', () => {
    const onClaim = jest.fn();
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ status: 'completed' })}
        variant="daily"
        onClaim={onClaim}
      />,
    );
    fireEvent.press(getByTestId('mission-claim-m1'));
    expect(onClaim).toHaveBeenCalledWith('m1');
  });
});

// Bug 1 (wave 4 — PO ticket 2026-05-12) — the claim button label is
// trimmed from ``+N CAB`` to just ``+N``. The gold styling + the
// header CAB-balance pill make the unit unambiguous, and the fixed
// 80 px width prevents visual jitter across rows (`+5` vs `+500`).
// The legacy wave-3 « +N CAB » assertions are replaced (not deleted —
// same coverage, new label). The accessibilityLabel keeps the unit.
describe('MissionRowBB — Bug 1 wave 4 claim button label + width', () => {
  it('keeps the standalone CAB pill removed (started wave 3)', () => {
    const { queryByTestId } = render(
      <MissionRowBB
        mission={baseMission({ cab_reward: 42 })}
        variant="daily"
      />,
    );
    expect(queryByTestId('mission-row-m1-cab-pill')).toBeNull();
  });

  it('renders the claim button label with +N (no "CAB" suffix)', () => {
    const { getByText, queryByText } = render(
      <MissionRowBB
        mission={baseMission({ cab_reward: 42 })}
        variant="daily"
      />,
    );
    expect(getByText('+42')).toBeTruthy();
    expect(queryByText('+42 CAB')).toBeNull();
  });

  it('renders +N for weekly missions too', () => {
    const { getByText } = render(
      <MissionRowBB
        mission={baseMission({ cab_reward: 7, frequency: 'weekly' })}
        variant="weekly"
      />,
    );
    expect(getByText('+7')).toBeTruthy();
  });

  it('keeps the cab_reward_a11y label on the claim button (VoiceOver still announces the unit)', () => {
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ cab_reward: 42 })}
        variant="daily"
      />,
    );
    const btn = getByTestId('mission-claim-m1');
    expect(btn.props.accessibilityLabel).toMatch(/42/);
  });

  it('locks the claim button width at 80 px so +5 and +500 align visually', () => {
    function flattenStyle(s: unknown): Record<string, unknown> {
      if (!s) return {};
      if (Array.isArray(s)) {
        return s.reduce<Record<string, unknown>>(
          (acc, item) => Object.assign(acc, flattenStyle(item)),
          {},
        );
      }
      return s as Record<string, unknown>;
    }
    const { getByTestId } = render(
      <MissionRowBB
        mission={baseMission({ cab_reward: 5 })}
        variant="daily"
      />,
    );
    const btn = getByTestId('mission-claim-m1');
    const flat = flattenStyle(btn.props.style);
    // The button may flatten width OR minWidth depending on the Button
    // implementation — we lock the visual width on either axis.
    const width = flat.width ?? flat.minWidth;
    expect(width).toBe(80);
  });
});

describe('MissionRowBB — Bug 2 greyed state for incomplete missions', () => {
  function flattenStyle(s: unknown): Record<string, unknown> {
    if (!s) return {};
    if (Array.isArray(s)) {
      return s.reduce<Record<string, unknown>>(
        (acc, item) => Object.assign(acc, flattenStyle(item)),
        {},
      );
    }
    return s as Record<string, unknown>;
  }

  it.each(['pending', 'active'] as const)(
    'dims the row when status is %s',
    (status) => {
      const { getByTestId } = render(
        <MissionRowBB
          mission={baseMission({ status })}
          variant="daily"
        />,
      );
      const row = getByTestId('mission-row-m1');
      const flat = flattenStyle(row.props.style);
      expect(flat.opacity).toBe(0.55);
    },
  );

  it.each(['completed', 'claimed'] as const)(
    'does NOT dim the row when status is %s',
    (status) => {
      const { getByTestId } = render(
        <MissionRowBB
          mission={baseMission({ status })}
          variant="daily"
        />,
      );
      const row = getByTestId('mission-row-m1');
      const flat = flattenStyle(row.props.style);
      // Opacity is either undefined or 1 (full colour) for done rows.
      expect(flat.opacity === undefined || flat.opacity === 1).toBe(true);
    },
  );
});

describe('MissionRowBB — back-compat with bare DailyMission (no BB fields)', () => {
  it('does not render any BB UI when fields are absent', () => {
    const bare: DailyMission = {
      id: 'bare',
      action_type: 'receipt_scan',
      difficulty: 'easy',
      target_count: 1,
      current_count: 0,
      cab_reward: 50,
      xp_reward: 5,
      status: 'active',
    };
    const { queryByTestId } = render(
      <MissionRowBB mission={bare} variant="daily" />,
    );
    expect(queryByTestId('mission-row-bare-buffer-btn')).toBeNull();
    expect(queryByTestId('mission-row-bare-buffer-badge')).toBeNull();
    expect(queryByTestId('mission-row-bare-burst-overlay')).toBeNull();
  });
});
