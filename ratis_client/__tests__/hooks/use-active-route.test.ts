import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useActiveRoute } from '@/hooks/use-active-route';
import { listClient } from '@/services/list-client';
import { AuthError } from '@/types/auth';

jest.mock('@/services/list-client', () => ({
  listClient: { get: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const LIST_ID = '11111111-1111-1111-1111-111111111111';

const ROUTE_READY = {
  id: 'route-1',
  list_id: LIST_ID,
  status: 'ready' as const,
  total_price: 24.6,
  total_savings: 5.2,
  distance_km: 3.2,
  computed_at: '2026-04-21T10:00:00+00:00',
  expires_at: '2026-04-22T10:00:00+00:00',
  stores: [],
  route_polyline: null,
  warnings: [],
};

describe('useActiveRoute', () => {
  beforeEach(() => {
    (listClient.get as jest.Mock).mockReset();
  });

  it('fetches the latest route when ready', async () => {
    (listClient.get as jest.Mock).mockResolvedValue(ROUTE_READY);
    const { result } = renderHook(() => useActiveRoute(LIST_ID), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listClient.get).toHaveBeenCalledWith(`/lists/${LIST_ID}/route`);
    expect(result.current.data).toEqual(ROUTE_READY);
  });

  it('returns null on 404 (no active route)', async () => {
    (listClient.get as jest.Mock).mockRejectedValue(
      new AuthError('no_active_route', 'VALIDATION_ERROR', 404),
    );
    const { result } = renderHook(() => useActiveRoute(LIST_ID), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('propagates non-404 errors', async () => {
    (listClient.get as jest.Mock).mockRejectedValue(
      new AuthError('server_error', 'SERVER_ERROR', 500),
    );
    const { result } = renderHook(() => useActiveRoute(LIST_ID), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('does not fire when listId is null', () => {
    (listClient.get as jest.Mock).mockResolvedValue(ROUTE_READY);
    renderHook(() => useActiveRoute(null), { wrapper: makeWrapper() });
    expect(listClient.get).not.toHaveBeenCalled();
  });

  it('returns slim payload when status is computing', async () => {
    const slim = { id: 'route-1', list_id: LIST_ID, status: 'computing' as const };
    (listClient.get as jest.Mock).mockResolvedValue(slim);
    const { result } = renderHook(() => useActiveRoute(LIST_ID), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(slim);
  });
});
