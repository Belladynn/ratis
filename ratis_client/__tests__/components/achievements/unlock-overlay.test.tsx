// ratis_client/__tests__/components/achievements/unlock-overlay.test.tsx
//
// Achievements V1 — overlay provider integration tests (PR 8/8).
//
// The overlay subscribes to the bus, queues incoming payloads, and renders
// the toast → modal/bespoke chain. We assert the wiring (FIFO queue, toast
// always shown, modal only for emerald+, bespoke for registered codes)
// without driving the full animation timeline.
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';

import {
  AchievementUnlockOverlay,
  MAX_TOAST_QUEUE,
  SUMMARY_TOAST_CODE,
} from '@/components/achievements/unlock-overlay';
import {
  achievementBus,
  dispatchAchievementUnlocked,
} from '@/services/achievement-notification-handler';
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

const TERRACOTTA: AchievementUnlockedPayload = {
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

const SAPPHIRE: AchievementUnlockedPayload = {
  ...TERRACOTTA,
  achievement_id: 'bbbb-2222',
  code: 'r_30',
  label: 'Mois sans rater',
  description: 'Streak de 30 jours',
  rarity: 'sapphire',
  cab_granted: 250,
  show_modal: true,
  sound_intensity: 2,
};

const KONAMI: AchievementUnlockedPayload = {
  ...TERRACOTTA,
  achievement_id: 'cccc-3333',
  code: 'sec_konami',
  label: 'Konami',
  description: 'Mémoire d\'éléphant',
  rarity: 'diamond',
  category: 'secret',
  icon: '🎮',
  cab_granted: 1200,
  show_modal: true,
  has_bespoke: true,
  sound_intensity: 3,
};

describe('AchievementUnlockOverlay', () => {
  beforeEach(() => {
    achievementBus.clear();
  });

  it('renders nothing when the bus is idle', () => {
    const { queryByTestId } = render(<AchievementUnlockOverlay />);
    expect(queryByTestId('achievement-unlock-toast')).toBeNull();
    expect(queryByTestId('achievement-celebration-modal')).toBeNull();
  });

  it('shows the toast when an unlock is dispatched', async () => {
    const { findByTestId } = render(<AchievementUnlockOverlay />);
    await act(async () => {
      await dispatchAchievementUnlocked(TERRACOTTA);
    });
    expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();
  });

  it('queues a celebration modal for sapphire (show_modal=true)', async () => {
    const { findByTestId } = render(<AchievementUnlockOverlay />);
    await act(async () => {
      await dispatchAchievementUnlocked(SAPPHIRE);
    });
    expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();
    expect(await findByTestId('achievement-celebration-modal')).toBeTruthy();
  });

  it('renders the bespoke component when has_bespoke + code matches', async () => {
    const { findByTestId, queryByTestId } = render(
      <AchievementUnlockOverlay />,
    );
    await act(async () => {
      await dispatchAchievementUnlocked(KONAMI);
    });
    // Bespoke wins over the generic celebration modal.
    expect(await findByTestId('bespoke-konami')).toBeTruthy();
    expect(queryByTestId('achievement-celebration-modal')).toBeNull();
  });

  it('does not render a modal for terracotta', async () => {
    const { findByTestId, queryByTestId } = render(
      <AchievementUnlockOverlay />,
    );
    await act(async () => {
      await dispatchAchievementUnlocked(TERRACOTTA);
    });
    expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();
    expect(queryByTestId('achievement-celebration-modal')).toBeNull();
  });

  it('cleans up bus subscription on unmount', async () => {
    const { unmount } = render(<AchievementUnlockOverlay />);
    unmount();
    // Dispatching after unmount must not throw / call disposed setState.
    await act(async () => {
      await dispatchAchievementUnlocked(TERRACOTTA);
    });
    // No assertion needed beyond "no throw".
  });

  // -------------------------------------------------------------------------
  // V1.1 — toast queue cap + summary toast
  // -------------------------------------------------------------------------
  describe('toast queue cap (V1.1)', () => {
    /**
     * Read the current toast queue length by counting how many `dismissToast`
     * calls drain it. We render the overlay, dispatch a known number of
     * unlocks, then tap the toast (= dismiss) until none remain ; the count
     * is the queue length at start.
     */
    async function dispatchN(
      n: number,
      base: AchievementUnlockedPayload = TERRACOTTA,
    ) {
      for (let i = 0; i < n; i++) {
        await act(async () => {
          await dispatchAchievementUnlocked({
            ...base,
            achievement_id: `seq-${i}`,
            label: `Trophée ${i}`,
          });
        });
      }
    }

    it('exposes a sane cap (10) so 11min batch toast spams cannot happen', () => {
      // Sanity check on the contract — if someone bumps the cap to 100
      // accidentally, this test fails loudly.
      expect(MAX_TOAST_QUEUE).toBe(10);
    });

    it('queues up to MAX_TOAST_QUEUE toasts and drops the surplus', async () => {
      const { findByTestId, getByTestId, queryByTestId } = render(
        <AchievementUnlockOverlay />,
      );

      // Dispatch exactly MAX (10) — all should fit. None dropped → no
      // summary toast at the end.
      await dispatchN(MAX_TOAST_QUEUE);
      expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();

      // Drain — tap-to-dismiss MAX times. After the last one queue should be
      // empty (no summary).
      for (let i = 0; i < MAX_TOAST_QUEUE; i++) {
        await act(async () => {
          fireEvent.press(getByTestId('achievement-unlock-toast'));
        });
      }
      expect(queryByTestId('achievement-unlock-toast')).toBeNull();
    });

    it('renders a summary "+N trophées débloqués" toast when overflow happens', async () => {
      // 15 dispatched → 10 toasts + 1 summary ("+5").
      const { findByTestId, getByTestId, queryByTestId, getByText } = render(
        <AchievementUnlockOverlay />,
      );

      const overflow = MAX_TOAST_QUEUE + 5;
      await dispatchN(overflow);
      expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();

      // Drain the 10 normal toasts.
      for (let i = 0; i < MAX_TOAST_QUEUE; i++) {
        await act(async () => {
          fireEvent.press(getByTestId('achievement-unlock-toast'));
        });
      }

      // After draining the 10 normal toasts the summary should appear.
      expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();
      expect(getByText('+5 trophées débloqués 🏆')).toBeTruthy();

      // Dismiss the summary — queue is now truly empty.
      await act(async () => {
        fireEvent.press(getByTestId('achievement-unlock-toast'));
      });
      expect(queryByTestId('achievement-unlock-toast')).toBeNull();
    });

    it('resets the dropped counter after rendering the summary', async () => {
      // Burst 1 : 13 dispatched → drain → "+3" summary.
      const { findByTestId, getByTestId, getByText } = render(
        <AchievementUnlockOverlay />,
      );
      await dispatchN(MAX_TOAST_QUEUE + 3);
      for (let i = 0; i < MAX_TOAST_QUEUE; i++) {
        await act(async () => {
          fireEvent.press(getByTestId('achievement-unlock-toast'));
        });
      }
      expect(getByText('+3 trophées débloqués 🏆')).toBeTruthy();
      // Dismiss the summary.
      await act(async () => {
        fireEvent.press(getByTestId('achievement-unlock-toast'));
      });

      // Burst 2 : only 5 dispatched → no overflow → NO summary toast at
      // drain. Counter from burst 1 must NOT carry over.
      await dispatchN(5);
      expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();
      for (let i = 0; i < 5; i++) {
        await act(async () => {
          fireEvent.press(getByTestId('achievement-unlock-toast'));
        });
      }
      // Queue empty, no summary should have appeared this burst.
      expect(
        () => getByText(/trophées débloqués/),
      ).toThrow();
    });

    it('uses the SUMMARY_TOAST_CODE sentinel on the summary payload', async () => {
      // Indirect check : after overflow + drain, the displayed accessibility
      // label uses our canonical pattern. We don't expose the payload from
      // the component, but the toast's accessibilityLabel includes the
      // payload's `label` which is built off SUMMARY_TOAST_CODE.
      expect(SUMMARY_TOAST_CODE).toBe('__achievements_summary__');
      const { findByTestId, getByTestId, getByLabelText } = render(
        <AchievementUnlockOverlay />,
      );
      await dispatchN(MAX_TOAST_QUEUE + 1);
      for (let i = 0; i < MAX_TOAST_QUEUE; i++) {
        await act(async () => {
          fireEvent.press(getByTestId('achievement-unlock-toast'));
        });
      }
      expect(await findByTestId('achievement-unlock-toast')).toBeTruthy();
      // a11y label is "Succès débloqué : <label>".
      expect(
        getByLabelText('Succès débloqué : +1 trophées débloqués 🏆'),
      ).toBeTruthy();
    });
  });
});
