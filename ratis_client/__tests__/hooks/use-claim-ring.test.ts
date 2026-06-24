import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useClaimRing } from '@/hooks/use-claim-ring';
import { apiClient } from '@/services/api-client';

jest.mock('@/services/api-client', () => {
  const actual = jest.requireActual('@/services/api-client');
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  };
});

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe('useClaimRing', () => {
  beforeEach(() => {
    (apiClient.post as jest.Mock).mockReset();
  });

  it('POSTs /account/rings/claim and returns claimed payload', async () => {
    const payload = {
      animation: 'claimed' as const,
      rings_consumed: 1,
      pending_rings: 2,
      subscription_price_cents: 799,
    };
    (apiClient.post as jest.Mock).mockResolvedValue(payload);

    const { result } = renderHook(() => useClaimRing(), { wrapper: makeWrapper() });

    let returned: unknown;
    await act(async () => {
      returned = await result.current.mutateAsync();
    });

    expect(apiClient.post).toHaveBeenCalledWith('/account/rings/claim');
    expect(returned).toEqual(payload);
    await waitFor(() => expect(result.current.data?.animation).toBe('claimed'));
    expect(result.current.data?.rings_consumed).toBe(1);
  });

  it('returns nothing_to_claim when no pending rings', async () => {
    const payload = {
      animation: 'nothing_to_claim' as const,
      rings_consumed: 3,
      pending_rings: 0,
      subscription_price_cents: 799,
    };
    (apiClient.post as jest.Mock).mockResolvedValue(payload);

    const { result } = renderHook(() => useClaimRing(), { wrapper: makeWrapper() });

    let returned: unknown;
    await act(async () => {
      returned = await result.current.mutateAsync();
    });

    expect(returned).toEqual(payload);
    await waitFor(() => expect(result.current.data?.animation).toBe('nothing_to_claim'));
    expect(result.current.data?.pending_rings).toBe(0);
  });

  it('exposes error state on failure', async () => {
    (apiClient.post as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useClaimRing(), { wrapper: makeWrapper() });

    await act(async () => {
      try {
        await result.current.mutateAsync();
      } catch {
        // expected
      }
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
