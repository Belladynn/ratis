// ratis_client/__tests__/app/leaderboard.test.tsx
//
// Buffer + Burst (refonte 2026-05-09) — Leaderboard screen tests.

import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('@/components/ui/screen-background-legacy', () => ({
  ScreenBackground: () => null,
}));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: jest.fn() }),
}));
jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => <>{children}</>,
}));

const mockState: {
  data: any;
  isLoading: boolean;
  isError: boolean;
  refetch: jest.Mock;
} = {
  data: undefined,
  isLoading: false,
  isError: false,
  refetch: jest.fn(),
};

jest.mock('@/hooks/use-burst-leaderboard', () => ({
  useBurstLeaderboard: jest.fn(() => mockState),
}));

import LeaderboardScreen from '@/app/leaderboard';
import { useBurstLeaderboard } from '@/hooks/use-burst-leaderboard';

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <LeaderboardScreen />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockState.data = undefined;
  mockState.isLoading = false;
  mockState.isError = false;
  mockState.refetch = jest.fn();
  (useBurstLeaderboard as jest.Mock).mockImplementation(() => mockState);
});

describe('LeaderboardScreen — rendering', () => {
  it('renders the back button + tabs + your-rank card', () => {
    mockState.data = { month: '2026-05', top: [], your_rank: null, your_max_xp: null };
    const { getByTestId } = renderScreen();
    expect(getByTestId('leaderboard-back')).toBeTruthy();
    expect(getByTestId('leaderboard-tabs')).toBeTruthy();
    expect(getByTestId('leaderboard-your-rank')).toBeTruthy();
  });

  it('shows the loading state when query is loading', () => {
    mockState.isLoading = true;
    const { getByTestId } = renderScreen();
    expect(getByTestId('leaderboard-loading')).toBeTruthy();
  });

  it('shows the error state when query fails', () => {
    mockState.isError = true;
    const { getByTestId } = renderScreen();
    expect(getByTestId('leaderboard-error')).toBeTruthy();
    expect(getByTestId('leaderboard-retry')).toBeTruthy();
  });

  it('shows the empty state when top is empty', () => {
    mockState.data = { month: '2026-05', top: [], your_rank: null, your_max_xp: null };
    const { getByTestId } = renderScreen();
    expect(getByTestId('leaderboard-empty')).toBeTruthy();
  });

  it('renders the leaderboard list with rows when top has entries', () => {
    mockState.data = {
      month: '2026-05',
      top: [
        {
          user_id: 'u1',
          display_name: 'alice',
          xp_earned: 65536,
          burst_count: 16,
          buffer_count: 0,
          mission_action_type: 'label_scan',
          mission_qualifier: null,
          recorded_at: '2026-05-08T12:00:00Z',
        },
        {
          user_id: 'u2',
          display_name: 'bob',
          xp_earned: 32768,
          burst_count: 15,
          buffer_count: 0,
          mission_action_type: 'receipt_scan',
          mission_qualifier: null,
          recorded_at: '2026-05-07T09:00:00Z',
        },
      ],
      your_rank: 23,
      your_max_xp: 4096,
    };
    const { getByTestId, getByText } = renderScreen();
    expect(getByTestId('leaderboard-list')).toBeTruthy();
    expect(getByTestId('leaderboard-row-0')).toBeTruthy();
    expect(getByTestId('leaderboard-row-1')).toBeTruthy();
    expect(getByText('alice')).toBeTruthy();
    expect(getByText('bob')).toBeTruthy();
  });
});

describe('LeaderboardScreen — interactions', () => {
  it('back button calls router.back()', () => {
    mockState.data = { month: '2026-05', top: [], your_rank: null, your_max_xp: null };
    const { getByTestId } = renderScreen();
    fireEvent.press(getByTestId('leaderboard-back'));
    expect(mockBack).toHaveBeenCalled();
  });

  it('tab change passes alltime to the hook', async () => {
    mockState.data = { month: '2026-05', top: [], your_rank: null, your_max_xp: null };
    const { getByTestId } = renderScreen();

    // Verify monthly is the initial call
    expect(useBurstLeaderboard).toHaveBeenCalledWith({ period: 'monthly' });

    fireEvent.press(getByTestId('leaderboard-tabs-tab-alltime'));

    await waitFor(() =>
      expect(useBurstLeaderboard).toHaveBeenCalledWith({ period: 'alltime' }),
    );
  });

  it('retry button calls refetch on error', () => {
    mockState.isError = true;
    const refetchSpy = jest.fn();
    mockState.refetch = refetchSpy;
    const { getByTestId } = renderScreen();
    fireEvent.press(getByTestId('leaderboard-retry'));
    expect(refetchSpy).toHaveBeenCalled();
  });
});
