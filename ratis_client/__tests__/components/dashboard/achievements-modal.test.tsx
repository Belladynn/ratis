// __tests__/components/dashboard/achievements-modal.test.tsx
//
// Restored in chunk 7 of visual iso V5 reconstruction. The V5 component
// lives at `components/profil/achievements-modal.tsx`. This file is kept
// under `__tests__/components/dashboard/` to preserve the original chunk-1
// skip placeholder location ; the pointer is the import path.

import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { AchievementsModal } from '@/components/profil/achievements-modal';
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
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const stub: readonly Achievement[] = [
  {
    id: 'a',
    label: 'Premier scan',
    description: 'desc',
    icon: '🎬',
    rarity: 'terracotta',
    category: 'volume',
    progress: 1,
    target: 1,
    status: 'unlocked',
  },
  {
    id: 'b',
    label: 'Cinquantaine',
    description: 'desc',
    icon: '📑',
    rarity: 'copper',
    category: 'volume',
    progress: 47,
    target: 50,
    status: 'in_progress',
  },
  {
    id: 'c',
    label: 'Recruteur',
    description: 'desc',
    icon: '🤝',
    rarity: 'bronze',
    category: 'social',
    progress: 0,
    target: 1,
    status: 'locked',
  },
];

describe('AchievementsModal (V5)', () => {
  it('renders the title and Collection eyebrow when open', () => {
    const { getByText } = render(
      <AchievementsModal
        open
        onClose={jest.fn()}
        achievements={stub}
      />,
    );
    expect(getByText('Succès')).toBeTruthy();
    expect(getByText(/Collection/i)).toBeTruthy();
  });

  it('renders the 3 stat pills', () => {
    const { getByText, getAllByText } = render(
      <AchievementsModal
        open
        onClose={jest.fn()}
        achievements={stub}
      />,
    );
    // 1 unlocked / 3 total in the stub.
    expect(getByText('1/3')).toBeTruthy();
    // "Débloqués" appears in both the stat pill and the status tab.
    expect(getAllByText('Débloqués').length).toBeGreaterThanOrEqual(1);
    // "En cours" is unique to the stat pill ; the status tab uses "En cours"
    // too, so use getAllByText.
    expect(getAllByText('En cours').length).toBeGreaterThanOrEqual(1);
    expect(getByText('Score')).toBeTruthy();
    expect(getByText('33%')).toBeTruthy();
  });

  it('exposes the 4 status filter tabs', () => {
    const { getByTestId } = render(
      <AchievementsModal
        open
        onClose={jest.fn()}
        achievements={stub}
      />,
    );
    expect(getByTestId('achievements-modal-status-all')).toBeTruthy();
    expect(getByTestId('achievements-modal-status-unlocked')).toBeTruthy();
    expect(getByTestId('achievements-modal-status-in_progress')).toBeTruthy();
    expect(getByTestId('achievements-modal-status-locked')).toBeTruthy();
  });

  it('filters by status when a tab is pressed', () => {
    const { getByTestId, queryByText } = render(
      <AchievementsModal
        open
        onClose={jest.fn()}
        achievements={stub}
      />,
    );
    expect(queryByText('Premier scan')).toBeTruthy();
    expect(queryByText('Recruteur')).toBeTruthy();

    fireEvent.press(getByTestId('achievements-modal-status-locked'));

    expect(queryByText('Premier scan')).toBeNull();
    expect(queryByText('Recruteur')).toBeTruthy();
  });

  it('exposes a "Toutes" category chip and per-category chips', () => {
    const { getByTestId } = render(
      <AchievementsModal
        open
        onClose={jest.fn()}
        achievements={stub}
      />,
    );
    expect(getByTestId('achievements-modal-cat-all')).toBeTruthy();
    expect(getByTestId('achievements-modal-cat-volume')).toBeTruthy();
    expect(getByTestId('achievements-modal-cat-social')).toBeTruthy();
  });

  it('calls onClose when the close button is pressed', () => {
    const onClose = jest.fn();
    const { getByTestId } = render(
      <AchievementsModal
        open
        onClose={onClose}
        achievements={stub}
      />,
    );
    fireEvent.press(getByTestId('achievements-modal-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows the empty-state copy when filters yield no result', () => {
    const onlyUnlocked: readonly Achievement[] = [stub[0]];
    const { getByTestId, getByText } = render(
      <AchievementsModal
        open
        onClose={jest.fn()}
        achievements={onlyUnlocked}
      />,
    );
    fireEvent.press(getByTestId('achievements-modal-status-locked'));
    expect(getByText(/Aucun succès/i)).toBeTruthy();
  });
});
