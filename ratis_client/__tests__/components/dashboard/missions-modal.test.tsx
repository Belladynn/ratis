// __tests__/components/dashboard/missions-modal.test.tsx
//
// Restored in chunk 7 of visual iso V5 reconstruction. The original test
// in commit 01d62ff targeted a V4 component shape (different prop API +
// different testIDs) — rewritten here for the V5 component built in
// `components/dashboard/missions-modal.tsx`.
//
// Surface under test :
//   - opens / closes via prop
//   - renders weekly + daily card sub-trees with the V5 testID convention
//     (`missions-modal-weekly` / `missions-modal-daily`)
//   - close button + backdrop both call onClose

import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { MissionsModal } from '@/components/dashboard/missions-modal';
import type { DailyMission } from '@/types/gamification';

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

const mkWeekly = (id = 'w1'): DailyMission => ({
  id,
  action_type: 'receipt_scan',
  difficulty: 'easy',
  target_count: 3,
  current_count: 0,
  cab_reward: 50,
  xp_reward: 10,
  status: 'active',
});

const mkDaily = (id = 'd1'): DailyMission => ({
  id,
  action_type: 'label_scan',
  difficulty: 'medium',
  target_count: 1,
  current_count: 0,
  cab_reward: 30,
  xp_reward: 5,
  status: 'active',
});

describe('MissionsModal (V5)', () => {
  it('renders both weekly and daily cards when open', () => {
    const { getByTestId } = render(
      <MissionsModal
        open
        onClose={jest.fn()}
        weekly={[mkWeekly()]}
        daily={[mkDaily()]}
      />,
    );
    expect(getByTestId('missions-modal-weekly')).toBeTruthy();
    expect(getByTestId('missions-modal-daily')).toBeTruthy();
  });

  it('renders the V5 eyebrow + title', () => {
    const { getByText } = render(
      <MissionsModal
        open
        onClose={jest.fn()}
        weekly={[]}
        daily={[]}
      />,
    );
    expect(getByText(/Tes missions/i)).toBeTruthy();
    expect(getByText(/Missions actives/i)).toBeTruthy();
  });

  it('calls onClose when the close button is pressed', () => {
    const onClose = jest.fn();
    const { getByTestId } = render(
      <MissionsModal
        open
        onClose={onClose}
        weekly={[]}
        daily={[]}
      />,
    );
    fireEvent.press(getByTestId('missions-modal-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when the backdrop is pressed', () => {
    const onClose = jest.fn();
    const { getByTestId } = render(
      <MissionsModal
        open
        onClose={onClose}
        weekly={[]}
        daily={[]}
      />,
    );
    fireEvent.press(getByTestId('missions-modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('returns null when fully closed', () => {
    const { queryByTestId } = render(
      <MissionsModal
        open={false}
        onClose={jest.fn()}
        weekly={[]}
        daily={[]}
      />,
    );
    expect(queryByTestId('missions-modal')).toBeNull();
  });
});
