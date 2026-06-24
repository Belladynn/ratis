// V5 dashboard tab — restored from git@01d62ff but adapted to the V5
// component tree. The original test asserted V4 testIDs (`dashboard-jar`,
// hardcoded V4 greeting strings) — V5 has different keys.
//
// We only smoke-test the tree to keep the surface stable :
//   - mounts without crash
//   - renders the V5 greeting "Bonjour"
//   - renders the V5 jar prestige hero
//   - renders the V5 missions block (or its skeleton path)
import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockRouterPush(...args) },
  useRouter: () => ({
    push: (...args: unknown[]) => mockRouterPush(...args),
    back: jest.fn(),
  }),
}));

jest.mock('@/components/ui/screen-background', () => ({
  ScreenBackground: () => null,
}));
jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

jest.mock('@/services/rewards-client', () => ({
  rewardsClient: {
    get: jest.fn().mockResolvedValue(null),
    post: jest.fn().mockResolvedValue({}),
  },
}));
jest.mock('@/services/api-client', () => ({
  apiClient: {
    get: jest.fn().mockResolvedValue(null),
    post: jest.fn().mockResolvedValue({}),
  },
}));
jest.mock('@/services/product-client', () => ({
  productClient: {
    get: jest.fn().mockRejectedValue(new Error('no-task')),
  },
}));
jest.mock('@/hooks/use-cab-balance', () => ({
  useCabBalance: () => ({ balance: 1240, battlepass: null, isLoading: false, isError: false }),
}));

import DashboardScreen from '@/app/(tabs)/index';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('DashboardScreen (V5)', () => {
  it('renders without crash', () => {
    const { toJSON } = renderWithQuery(<DashboardScreen />);
    expect(toJSON()).not.toBeNull();
  });

  it('renders the V5 greeting block', () => {
    const { getByText } = renderWithQuery(<DashboardScreen />);
    expect(getByText(/Bonjour/)).toBeTruthy();
  });

  it('renders the V5 jar prestige hero', () => {
    const { getByTestId } = renderWithQuery(<DashboardScreen />);
    expect(getByTestId('jar-prestige')).toBeTruthy();
  });

  it('wires the AppHeader 🎁 shop / 🏆 achievements / 📅 calendar buttons (Bug 3 + Bug 4)', () => {
    // Regression guard for the PO ticket 2026-05-12 — before this fix, the
    // three header icon buttons were rendered without `onPress` handlers, so
    // tapping them was a no-op. The Achievements modal and Missions modal
    // were unreachable from the dashboard despite being mounted in the tree.
    mockRouterPush.mockClear();
    const { getByTestId, queryByTestId } = renderWithQuery(<DashboardScreen />);
    // Achievements modal must mount once the trophy icon is pressed (it
    // exposes its own testID via the AchievementsModal component).
    fireEvent.press(getByTestId('app-header-achievements'));
    expect(queryByTestId('achievements-modal')).not.toBeNull();
    // Calendar icon opens the missions popup (canonical name : MissionsModal).
    fireEvent.press(getByTestId('app-header-calendar'));
    expect(queryByTestId('missions-modal')).not.toBeNull();
    // Bug 8 wave 2 — Shop icon is greyed (alpha-unavailable). Pressing it
    // must be a no-op until the Boutique is fully wired up.
    fireEvent.press(getByTestId('app-header-shop'));
    expect(mockRouterPush).not.toHaveBeenCalledWith('/shop');
    expect(
      getByTestId('app-header-shop').props.accessibilityState?.disabled,
    ).toBe(true);
  });

  // Bug 7 (PO ticket 2026-05-12 wave 2) — the JackStreakButton's feed CTA
  // must be wired to the `useFeedJack` mutation. Before this fix the
  // Pressable rendered but did nothing on press (looked « dead » to PO).
  it('wires the Feed Jack CTA to a POST /gamification/streak/feed call', async () => {
    const rewardsClient = require('@/services/rewards-client').rewardsClient;
    // The streak query needs a HUNGRY response so the feed CTA renders
    // (the fed-state CTA is disabled by design).
    (rewardsClient.get as jest.Mock).mockImplementation((path: string) => {
      if (path.includes('streak')) {
        return Promise.resolve({
          streak_days: 3,
          multiplier: 0.15,
          food_reserves: 0,
          already_fed_today: false,
          needs_repair: false,
          last_fed_at: null,
        });
      }
      return Promise.resolve(null);
    });
    (rewardsClient.post as jest.Mock).mockClear();
    (rewardsClient.post as jest.Mock).mockResolvedValue({
      streak_days: 4,
      multiplier: 0.2,
      food_reserves: 0,
      already_fed_today: true,
      needs_repair: false,
      last_fed_at: '2026-05-12T08:00:00Z',
    });
    const { findByTestId } = renderWithQuery(<DashboardScreen />);
    const cta = await findByTestId('jack-streak-feed-cta');
    fireEvent.press(cta);
    // The mutation routes to the canonical `/gamification/streak/feed`
    // endpoint with an empty body (timezone is optional in V1).
    await new Promise((r) => setTimeout(r, 0));
    expect(rewardsClient.post).toHaveBeenCalledWith(
      '/gamification/streak/feed',
      {},
    );
  });
});
