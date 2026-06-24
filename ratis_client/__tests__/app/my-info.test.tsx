import React from 'react';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

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
  isSuccess: false,
  isError: false,
  error: null as Error | null,
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
  created_at: '',
  updated_at: '',
};
jest.mock('@/hooks/use-auth-me', () => ({
  useAuthMe: () => ({ data: mockAuthMe, isLoading: false, isError: false, isSuccess: true }),
}));

// The "Comptes liés" section (H2 Phase 2) added new dependencies to the
// screen — stub them so the existing profile-section assertions stay isolated.
jest.mock('@/hooks/use-identities', () => ({
  useIdentities: () => ({ data: [], isLoading: false, isError: false }),
  useLinkProvider: () => ({ mutateAsync: jest.fn(), isPending: false }),
  useUnlinkProvider: () => ({ mutateAsync: jest.fn(), isPending: false }),
}));
jest.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ getProviderToken: jest.fn() }),
}));

import MyInfoScreen from '@/app/my-info';

function renderWithQuery(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockUpdateProfile.isPending = false;
  mockUpdateProfile.isSuccess = false;
  mockUpdateProfile.isError = false;
  mockUpdateProfile.error = null;
});

describe('MyInfoScreen', () => {
  it('renders the user email read-only', () => {
    const { getByText, getByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByText('Mes infos')).toBeTruthy();
    expect(getByTestId('my-info-email').props.children).toBe('alice@example.com');
  });

  it('prefills the display name input with the current value', () => {
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByTestId('my-info-display-name').props.value).toBe('Alice');
  });

  it('prefills the timezone input with the current value', () => {
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByTestId('my-info-timezone').props.value).toBe('Europe/Paris');
  });

  it('disables the Save button when no field changed', () => {
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    expect(getByTestId('my-info-save').props.accessibilityState?.disabled).toBe(true);
  });

  it('enables the Save button as soon as the display name is edited', () => {
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    fireEvent.changeText(getByTestId('my-info-display-name'), 'Alice v2');
    expect(getByTestId('my-info-save').props.accessibilityState?.disabled).toBe(false);
  });

  it('calls updateProfile with only the fields that changed', async () => {
    mockUpdateProfile.mutateAsync.mockResolvedValue({ ...mockAuthMe, display_name: 'Alice v2' });
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    fireEvent.changeText(getByTestId('my-info-display-name'), 'Alice v2');
    await act(async () => {
      fireEvent.press(getByTestId('my-info-save'));
    });
    await waitFor(() => {
      expect(mockUpdateProfile.mutateAsync).toHaveBeenCalledWith({ display_name: 'Alice v2' });
    });
  });

  it('rejects display name shorter than 1 char (client-side validation)', () => {
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    fireEvent.changeText(getByTestId('my-info-display-name'), '');
    expect(getByTestId('my-info-save').props.accessibilityState?.disabled).toBe(true);
  });

  it('rejects display name longer than 30 chars', () => {
    const { getByTestId } = renderWithQuery(<MyInfoScreen />);
    fireEvent.changeText(getByTestId('my-info-display-name'), 'x'.repeat(31));
    expect(getByTestId('my-info-save').props.accessibilityState?.disabled).toBe(true);
  });

});
