import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useUpdateProfile } from '@/hooks/use-update-profile';
import { apiClient } from '@/services/api-client';

jest.mock('@/services/api-client', () => ({
  apiClient: { patch: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('useUpdateProfile', () => {
  it('sends PATCH /account/profile with only the provided fields', async () => {
    (apiClient.patch as jest.Mock).mockResolvedValue({
      id: 'u1',
      email: 'alice@example.com',
      provider: 'google',
      display_name: 'Alice v2',
      avatar_url: null,
      timezone: 'Europe/Paris',
      current_level_id: null,
      created_at: '',
      updated_at: '',
    });

    const { result } = renderHook(() => useUpdateProfile(), { wrapper: makeWrapper() });
    result.current.mutate({ display_name: 'Alice v2' });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(apiClient.patch).toHaveBeenCalledWith('/account/profile', {
      display_name: 'Alice v2',
    });
  });

  it('omits undefined fields from the payload (partial patch semantics)', async () => {
    (apiClient.patch as jest.Mock).mockResolvedValue({});

    const { result } = renderHook(() => useUpdateProfile(), { wrapper: makeWrapper() });
    result.current.mutate({ timezone: 'America/New_York' });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [, payload] = (apiClient.patch as jest.Mock).mock.calls[0];
    expect(payload).toEqual({ timezone: 'America/New_York' });
    expect(payload).not.toHaveProperty('display_name');
    expect(payload).not.toHaveProperty('avatar_url');
  });

  it('surfaces errors from the API (e.g. 400 validation)', async () => {
    (apiClient.patch as jest.Mock).mockRejectedValue(new Error('display_name_too_long'));

    const { result } = renderHook(() => useUpdateProfile(), { wrapper: makeWrapper() });
    result.current.mutate({ display_name: 'x'.repeat(100) });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toContain('display_name_too_long');
  });

  it('invalidates auth-me query on success so the UI re-fetches fresh user', async () => {
    (apiClient.patch as jest.Mock).mockResolvedValue({});

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidateSpy = jest.spyOn(qc, 'invalidateQueries');
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client: qc }, children);

    const { result } = renderHook(() => useUpdateProfile(), { wrapper });
    result.current.mutate({ display_name: 'Alice' });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth-me'] });
  });
});
