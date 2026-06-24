// V5 missions-block — adapted from git@01d62ff (V4 had separate
// `isLoadingWeekly` / `isLoadingDaily` props ; V5 receives the slice arrays
// pre-resolved by the parent and renders synchronously).
import React from 'react';
import { render } from '@testing-library/react-native';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

import { MissionsBlock } from '@/components/dashboard/missions-block';
import type { DailyMission } from '@/types/gamification';

const mkWeekly = (): DailyMission => ({
  id: 'w1', action_type: 'receipt_scan', difficulty: 'easy',
  target_count: 3, current_count: 0, cab_reward: 50, xp_reward: 10, status: 'active',
});

const mkDaily = (): DailyMission => ({
  id: 'd1', action_type: 'label_scan', difficulty: 'medium',
  target_count: 1, current_count: 0, cab_reward: 30, xp_reward: 5, status: 'active',
});

describe('MissionsBlock', () => {
  it('renders both weekly and daily sections', () => {
    const { getAllByText } = render(
      <MissionsBlock
        weekly={[mkWeekly()]}
        daily={[mkDaily()]}
        onClaim={jest.fn()}
      />,
    );
    // Wording aligned with the PO directive 2026-05-12 (verb-first
    // imperative — « Scanne ... » instead of « Scanner ... »). See
    // `__tests__/utils/mission-labels.test.ts` for the canonical guard.
    expect(getAllByText('Scanne un ticket de caisse')).toBeTruthy();
    expect(getAllByText('Scanne une étiquette électronique')).toBeTruthy();
  });

  it('renders the chest SVG overlay (V5 visual layer)', () => {
    const { getByTestId } = render(
      <MissionsBlock
        weekly={[]}
        daily={[]}
        onClaim={jest.fn()}
      />,
    );
    // V5 testID convention : `<root-testID>-chest` (default root is
    // `missions-block`).
    expect(getByTestId('missions-block-chest')).toBeTruthy();
  });

  it('exposes weekly + daily sub-cards via testID', () => {
    const { getByTestId } = render(
      <MissionsBlock
        weekly={[]}
        daily={[]}
        onClaim={jest.fn()}
      />,
    );
    expect(getByTestId('missions-block-weekly')).toBeTruthy();
    expect(getByTestId('missions-block-daily')).toBeTruthy();
  });

  // Bug 3 (PO ticket 2026-05-12 wave 2) — the chest SVG was previously
  // letterboxed via `preserveAspectRatio="xMidYMid meet"`, which left it
  // floating in the middle band. The fix switches to `slice` (= cover)
  // so the chest spans the wrapper top-to-bottom.
  it('chest SVG uses preserveAspectRatio=slice so it spans both cards (Bug 3)', () => {
    const { getByTestId } = render(
      <MissionsBlock
        weekly={[mkWeekly()]}
        daily={[mkDaily()]}
        onClaim={jest.fn()}
      />,
    );
    const chest = getByTestId('missions-block-chest');
    // Walk the chest wrapper's children to find the ChestSvg props. The
    // svg-transformer-rendered React component exposes its props in the
    // test tree, so we can assert on `preserveAspectRatio` directly.
    const findProps = (node: any): any[] => {
      if (!node) return [];
      const out: any[] = [];
      if (node.props) out.push(node.props);
      const kids = Array.isArray(node.children) ? node.children : [];
      for (const k of kids) out.push(...findProps(k));
      return out;
    };
    const allProps = findProps(chest);
    const slicePropFound = allProps.some(
      (p) =>
        p.preserveAspectRatio === 'xMidYMid slice' ||
        p.preserveAspectRatio === 'slice',
    );
    expect(slicePropFound).toBe(true);
  });
});
