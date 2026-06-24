// __tests__/app/(tabs)/profil.test.tsx
//
// Restored in chunk 6 of visual iso V5 reconstruction (PR feat/visual-iso-v5).
// The Profil screen was rebuilt iso `Ratis_handoff/lib/ratis-other-tabs.jsx`
// lines 562-627 — see `app/(tabs)/profil.tsx`. The original test in commit
// 01d62ff already targeted the V5 contract (uppercase group labels, 3 stat
// tiles, the disabled stub rows, the SupportIdCard at the bottom, the ⚙
// settings button) ; it is restored verbatim.

import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ProfilScreen from '@/app/(tabs)/profil';

const mockSignOut = jest.fn();
jest.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({
    signOut: mockSignOut,
    signIn: jest.fn(),
    devSignIn: jest.fn(),
    status: 'authenticated',
    user: null,
    error: null,
  }),
}));

jest.mock('@/components/ui/screen-background', () => ({
  ScreenBackground: () => null,
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
      children?: unknown;
    }) => ReactMock.createElement(RN.View, props, children),
  };
});
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockRouterPush(...args) },
}));
jest.mock('@/services/rewards-client', () => ({
  rewardsClient: { get: jest.fn().mockResolvedValue(null) },
}));
jest.mock('@/hooks/use-cab-balance', () => ({
  useCabBalance: () => ({
    balance: 1240,
    battlepass: null,
    isLoading: false,
    isError: false,
  }),
}));
jest.mock('@/hooks/use-account-stats', () => ({
  useAccountStats: () => ({
    data: {
      total_scans: 142,
      unique_products: 98,
      total_savings_cents: 3400,
      member_since: '2025-11-01T10:00:00+00:00',
    },
    isLoading: false,
    isError: false,
    isSuccess: true,
  }),
}));
jest.mock('@/hooks/use-battlepass', () => ({
  useBattlepass: () => ({
    data: {
      season_name: 'S1',
      current_level: 0,
      xp_current: 0,
      xp_next_level: 100,
      next_reward_label: '',
      next_reward_type: null,
    },
    isLoading: false,
    isError: false,
    isSuccess: true,
  }),
}));
jest.mock('@/hooks/use-auth-me', () => ({
  useAuthMe: () => ({
    data: {
      id: 'u1',
      email: 'alice@example.com',
      account_type: 'oauth',
      display_name: 'Alice',
      avatar_url: null,
      timezone: 'Europe/Paris',
      current_level_id: null,
      support_id: 'RTS-A3K7XP',
      created_at: '',
      updated_at: '',
    },
    isLoading: false,
    isError: false,
    isSuccess: true,
  }),
}));

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('ProfilScreen (V5 strict iso)', () => {
  it('renders user identity (name + handle + level badge)', () => {
    const { getByText } = renderWithQuery(<ProfilScreen />);
    expect(getByText('Alice')).toBeTruthy();
    expect(getByText('@alice')).toBeTruthy();
    // Level badge format "★ Niv. {n}" — battlepass mock returns level 0
    expect(getByText(/★ Niv\./)).toBeTruthy();
  });

  it('renders 3 stat tiles with V5 uppercase labels', () => {
    const { getByText, getAllByText } = renderWithQuery(<ProfilScreen />);
    // Balance "1 240" appears in both AppHeader and the CAB stat tile.
    expect(getAllByText('1 240').length).toBeGreaterThanOrEqual(1);
    expect(getByText('142')).toBeTruthy();
    expect(getByText('34€')).toBeTruthy();
    expect(getByText('CAB')).toBeTruthy();
    expect(getByText('SCANS')).toBeTruthy();
    expect(getByText('ÉCONOMIES')).toBeTruthy();
  });

  it('renders the 3 V5 menu groups RÉCOMPENSES / COMPTE / SESSION', () => {
    const { getByText } = renderWithQuery(<ProfilScreen />);
    expect(getByText('RÉCOMPENSES')).toBeTruthy();
    expect(getByText('COMPTE')).toBeTruthy();
    expect(getByText('SESSION')).toBeTruthy();
  });

  it('renders all V5 menu items', () => {
    const { getByText } = renderWithQuery(<ProfilScreen />);
    expect(getByText('Boutique')).toBeTruthy();
    expect(getByText('Succès')).toBeTruthy();
    expect(getByText('Parrainage')).toBeTruthy();
    expect(getByText('Mes informations')).toBeTruthy();
    expect(getByText('Notifications')).toBeTruthy();
    expect(getByText('Se déconnecter')).toBeTruthy();
  });

  it('calls AuthContext.signOut when the logout row is pressed', () => {
    mockSignOut.mockClear();
    const { getByText } = renderWithQuery(<ProfilScreen />);
    fireEvent.press(getByText('Se déconnecter'));
    expect(mockSignOut).toHaveBeenCalledTimes(1);
  });

  it('greys out Notifications + Boutique ; Achievements, Parrainage, MyInfo are enabled in V1', () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    // Notifications remains a V2 stub.
    expect(
      getByTestId('profil-row-notifications').props.accessibilityState
        ?.disabled,
    ).toBe(true);
    // Bug 8 (PO ticket 2026-05-12 wave 2) — Boutique is alpha-unavailable.
    // The row is greyed + non-pressable until the Runa KYB lands.
    expect(
      getByTestId('profil-row-shop').props.accessibilityState?.disabled,
    ).toBe(true);
    // Other active rows
    expect(
      getByTestId('profil-row-achievements').props.accessibilityState?.disabled,
    ).toBeFalsy();
    expect(
      getByTestId('profil-row-referral').props.accessibilityState?.disabled,
    ).toBeFalsy();
    expect(
      getByTestId('profil-row-my-info').props.accessibilityState?.disabled,
    ).toBeFalsy();
    expect(
      getByTestId('profil-row-logout').props.accessibilityState?.disabled,
    ).toBeFalsy();
  });

  it("renders the SupportIdCard with the user's support_id at the bottom", () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    const card = getByTestId('support-id-card');
    expect(card).toBeTruthy();
    expect(getByTestId('support-id-value').children[0]).toBe('RTS-A3K7XP');
  });

  it('does not navigate when pressing a disabled stub row', () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    mockSignOut.mockClear();
    mockRouterPush.mockClear();
    fireEvent.press(getByTestId('profil-row-notifications'));
    expect(mockSignOut).not.toHaveBeenCalled();
    expect(mockRouterPush).not.toHaveBeenCalled();
  });

  it('does NOT navigate to /shop when pressing the disabled Boutique row (Bug 8)', () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    mockRouterPush.mockClear();
    fireEvent.press(getByTestId('profil-row-shop'));
    // Bug 8 — the row is disabled (alpha-unavailable). Pressing it must
    // be a no-op until the Boutique is wired up.
    expect(mockRouterPush).not.toHaveBeenCalledWith('/shop');
  });

  it('renders the Leaderboard menu row and navigates to /leaderboard', () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    mockRouterPush.mockClear();
    const row = getByTestId('profil-row-leaderboard');
    expect(row).toBeTruthy();
    fireEvent.press(row);
    expect(mockRouterPush).toHaveBeenCalledWith('/leaderboard');
  });

  it('renders the version trio badge', () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    expect(getByTestId('profil-version-trio')).toBeTruthy();
  });

  it('renders the settings ⚙ button in the page title band', () => {
    const { getByTestId } = renderWithQuery(<ProfilScreen />);
    expect(getByTestId('profil-settings-btn')).toBeTruthy();
  });

  it('wires the AppHeader 🏆 achievements + 📅 calendar buttons (PO ticket Bug 3 + Bug 4)', () => {
    // Before this fix the header trophy/calendar icons were rendered as
    // dead pixels — they had no `onPress`, so neither the AchievementsModal
    // nor the MissionsModal could open from the profil screen.
    const { getByTestId, queryByTestId } = renderWithQuery(<ProfilScreen />);
    fireEvent.press(getByTestId('app-header-achievements'));
    expect(queryByTestId('achievements-modal')).not.toBeNull();
    fireEvent.press(getByTestId('app-header-calendar'));
    expect(queryByTestId('missions-modal')).not.toBeNull();
  });

  it('opens the AchievementsModal when the Succès row is pressed (Bug 3)', () => {
    const { getByTestId, queryByTestId } = renderWithQuery(<ProfilScreen />);
    fireEvent.press(getByTestId('profil-row-achievements'));
    expect(queryByTestId('achievements-modal')).not.toBeNull();
  });

  it('shows a non-disabled Succès subtitle even when the catalogue is empty (Bug 3)', () => {
    // With the achievements query mocked to return null (see top-level
    // `rewardsClient.get` mock), `achievementsTotal === 0`. The previous
    // copy was « Bientôt » which read as "coming soon / disabled" to
    // alpha testers — the fix is an inviting fallback.
    const { getByText } = renderWithQuery(<ProfilScreen />);
    expect(getByText(/Découvre tes succès|débloqués/)).toBeTruthy();
  });
});
