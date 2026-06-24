// __tests__/app/linked-accounts.test.tsx
//
// Covers the "Comptes liés" section of the profile screen (my-info.tsx,
// H2 Phase 2 — Task 10). Verifies the linked/unlinked rendering, the link
// flow (getProviderToken → link mutation), the unlink affordance, and the
// backend-error mapping (cannot_unlink_last_identity).

import React from 'react';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthError } from '@/types/auth';
import type { Identity } from '@/hooks/use-identities';

jest.mock('@/components/ui/screen-background-legacy', () => ({
  ScreenBackground: () => null,
}));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('expo-router', () => ({
  router: { back: jest.fn() },
  useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
}));

const mockUpdateProfile = {
  mutateAsync: jest.fn(),
  isPending: false,
};
jest.mock('@/hooks/use-update-profile', () => ({
  useUpdateProfile: () => mockUpdateProfile,
}));

const mockAuthMe = {
  id: 'u1',
  email: 'alice@example.com',
  account_type: 'oauth',
  display_name: 'Alice',
  avatar_url: null,
  timezone: 'Europe/Paris',
  current_level_id: null,
  support_id: 'RTS-ABCDEF',
  created_at: '',
  updated_at: '',
};
jest.mock('@/hooks/use-auth-me', () => ({
  useAuthMe: () => ({ data: mockAuthMe, isLoading: false, isError: false, isSuccess: true }),
}));

// ── Identity hooks ──────────────────────────────────────────────────────────
let mockIdentities: Identity[] = [];
const mockLinkMutateAsync = jest.fn();
const mockUnlinkMutateAsync = jest.fn();
jest.mock('@/hooks/use-identities', () => ({
  useIdentities: () => ({ data: mockIdentities, isLoading: false, isError: false }),
  useLinkProvider: () => ({ mutateAsync: mockLinkMutateAsync, isPending: false }),
  useUnlinkProvider: () => ({ mutateAsync: mockUnlinkMutateAsync, isPending: false }),
}));

// ── AuthContext.getProviderToken ────────────────────────────────────────────
const mockGetProviderToken = jest.fn();
jest.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ getProviderToken: mockGetProviderToken }),
}));

import MyInfoScreen from '@/app/my-info';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const googleIdentity: Identity = {
  provider: 'google',
  email: 'alice@gmail.com',
  created_at: '2026-01-01T00:00:00Z',
};
const appleIdentity: Identity = {
  provider: 'apple',
  email: 'alice@icloud.com',
  created_at: '2026-02-01T00:00:00Z',
};

beforeEach(() => {
  jest.clearAllMocks();
  mockUpdateProfile.isPending = false;
  mockIdentities = [];
});

describe('MyInfoScreen — Comptes liés section', () => {
  it('renders the "Comptes liés" section title', () => {
    mockIdentities = [googleIdentity];
    const { getByText } = renderWithQuery(<MyInfoScreen />);
    expect(getByText('Comptes liés')).toBeTruthy();
  });

  it('shows a "Lié" badge on a linked provider row', () => {
    mockIdentities = [googleIdentity];
    const { getByTestId, queryByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByTestId('linked-badge-google')).toBeTruthy();
    // Apple is not linked → no badge.
    expect(queryByTestId('linked-badge-apple')).toBeNull();
  });

  it('shows a "Lier Apple" button when Apple is not linked', () => {
    mockIdentities = [googleIdentity];
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByTestId('link-btn-apple')).toBeTruthy();
  });

  it('links Apple: getProviderToken("apple") then the link mutation', async () => {
    mockIdentities = [googleIdentity];
    mockGetProviderToken.mockResolvedValue('apple-raw-id-token');
    mockLinkMutateAsync.mockResolvedValue(undefined);
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);

    await act(async () => {
      fireEvent.press(getByTestId('link-btn-apple'));
    });

    await waitFor(() => {
      expect(mockGetProviderToken).toHaveBeenCalledWith('apple');
      expect(mockLinkMutateAsync).toHaveBeenCalledWith({
        provider: 'apple',
        token: 'apple-raw-id-token',
      });
    });
  });

  it('shows a "Délier" affordance for each linked provider when both are linked', () => {
    mockIdentities = [googleIdentity, appleIdentity];
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByTestId('unlink-btn-google')).toBeTruthy();
    expect(getByTestId('unlink-btn-apple')).toBeTruthy();
  });

  it('surfaces error_cannot_unlink_last when the backend rejects the last unlink', async () => {
    mockIdentities = [googleIdentity];
    // Confirm dialog: invoke the destructive ("Délier") button callback.
    const alertSpy = jest
      .spyOn(Alert, 'alert')
      .mockImplementation((_title, _msg, buttons) => {
        const confirm = buttons?.find((b) => b.style === 'destructive');
        confirm?.onPress?.();
      });
    mockUnlinkMutateAsync.mockRejectedValue(
      new AuthError('cannot_unlink_last_identity', 'VALIDATION_ERROR', 409),
    );
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);

    await act(async () => {
      fireEvent.press(getByTestId('unlink-btn-google'));
    });

    await waitFor(() => {
      expect(getByTestId('linked-accounts-feedback').props.children).toBe(
        'Impossible de délier ton dernier moyen de connexion.',
      );
    });
    alertSpy.mockRestore();
  });
});
