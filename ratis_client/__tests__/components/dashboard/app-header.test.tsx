// V5 dashboard sticky header (renamed from `dashboard-header-bar` in V4).
// Fresh test — V4 had a different prop shape (greeting / contextual line
// belonged to the header). V5 keeps the header to season + balance + 3
// icon buttons.
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children, testID }: any) => {
    const { View } = require('react-native');
    return <View testID={testID}>{children}</View>;
  },
}));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@/hooks/use-cab-balance', () => ({
  useCabBalance: () => ({ balance: 12480, battlepass: null, isLoading: false, isError: false }),
}));
jest.mock('@/hooks/use-battlepass', () => ({
  useBattlepass: () => ({
    data: {
      season_name: 'Printemps 2026',
      current_level: 12,
      xp_current: 340,
      xp_next_level: 500,
      next_reward_label: 'Skin doré',
      next_reward_type: 'skin',
    },
    isLoading: false,
  }),
}));

import { AppHeader, formatBalanceN } from '@/components/dashboard/app-header';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('AppHeader', () => {
  it('renders the season label with the battlepass level', () => {
    const { getByTestId } = renderWithQuery(<AppHeader />);
    expect(getByTestId('app-header-season-label').props.children.join('')).toMatch(
      /SAISON · NIV\. 12/,
    );
  });

  it('renders the formatted CAB balance', () => {
    const { getByText } = renderWithQuery(<AppHeader />);
    // formatBalanceN inserts non-breaking spaces — query the same way.
    expect(getByText(formatBalanceN(12480))).toBeTruthy();
  });

  it('shows badges only when count > 0', () => {
    const { getByTestId, queryByText } = renderWithQuery(
      <AppHeader achievementsBadge={21} calendarBadge={0} />,
    );
    expect(getByTestId('app-header-achievements')).toBeTruthy();
    // 21 badge text rendered for achievements ; calendar badge has no text.
    expect(queryByText('21')).toBeTruthy();
  });

  it('routes achievements / calendar press to the right handlers (shop disabled by default)', () => {
    const onShop = jest.fn();
    const onAchievements = jest.fn();
    const onCalendar = jest.fn();
    const { getByTestId } = renderWithQuery(
      <AppHeader
        onShop={onShop}
        onAchievements={onAchievements}
        onCalendar={onCalendar}
      />,
    );
    fireEvent.press(getByTestId('app-header-shop'));
    fireEvent.press(getByTestId('app-header-achievements'));
    fireEvent.press(getByTestId('app-header-calendar'));
    // Bug 8 (PO ticket 2026-05-12 wave 2) — `shopDisabled` defaults to
    // true, so the 🎁 icon is greyed and pressing it is a no-op.
    expect(onShop).not.toHaveBeenCalled();
    expect(onAchievements).toHaveBeenCalledTimes(1);
    expect(onCalendar).toHaveBeenCalledTimes(1);
  });

  it('routes shop press when shopDisabled=false (V2 opt-in)', () => {
    const onShop = jest.fn();
    const { getByTestId } = renderWithQuery(
      <AppHeader onShop={onShop} shopDisabled={false} />,
    );
    fireEvent.press(getByTestId('app-header-shop'));
    expect(onShop).toHaveBeenCalledTimes(1);
  });

  // Bug 8 — accessibility state reflects the disabled flag so screen
  // readers + tests can detect the alpha-unavailable shop icon.
  it('exposes accessibilityState.disabled=true on the shop icon by default', () => {
    const { getByTestId } = renderWithQuery(<AppHeader />);
    expect(
      getByTestId('app-header-shop').props.accessibilityState?.disabled,
    ).toBe(true);
  });
});

describe('formatBalanceN', () => {
  it('returns the number as-is below 1000', () => {
    expect(formatBalanceN(0)).toBe('0');
    expect(formatBalanceN(999)).toBe('999');
  });
  it('inserts non-breaking spaces every 3 digits', () => {
    // U+00A0 NBSP separator — mirrors the JSX `formatBalance` helper.
    const NBSP = '\u00A0';
    expect(formatBalanceN(12480)).toBe(`12${NBSP}480`);
    expect(formatBalanceN(1234567)).toBe(`1${NBSP}234${NBSP}567`);
  });
});
