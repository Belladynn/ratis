import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useOptimizeRoute } from '@/hooks/use-optimize-route';
import { listClient } from '@/services/list-client';

jest.mock('@/services/list-client', () => ({
  listClient: { post: jest.fn() },
}));

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function makeWrapper(qc: QueryClient) {
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const LIST_ID = '11111111-1111-1111-1111-111111111111';

describe('useOptimizeRoute', () => {
  beforeEach(() => {
    (listClient.post as jest.Mock).mockReset();
  });

  it('triggers optimization with position and returns computing response', async () => {
    const resp = { id: 'route-1', list_id: LIST_ID, status: 'computing' as const };
    (listClient.post as jest.Mock).mockResolvedValue(resp);

    const qc = makeClient();
    const { result } = renderHook(() => useOptimizeRoute(LIST_ID), {
      wrapper: makeWrapper(qc),
    });

    let payload: Awaited<ReturnType<typeof result.current.mutateAsync>> | undefined;
    await act(async () => {
      payload = await result.current.mutateAsync({ lat: 48.86, lng: 2.38 });
    });

    expect(listClient.post).toHaveBeenCalledWith(
      `/lists/${LIST_ID}/optimize`,
      { lat: 48.86, lng: 2.38 },
    );
    expect(payload).toEqual(resp);
  });

  it('invalidates route cache on success', async () => {
    const resp = { id: 'route-1', list_id: LIST_ID, status: 'computing' as const };
    (listClient.post as jest.Mock).mockResolvedValue(resp);

    const qc = makeClient();
    const spy = jest.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useOptimizeRoute(LIST_ID), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.mutateAsync({ lat: 48.86, lng: 2.38 });
    });

    expect(spy).toHaveBeenCalledWith({ queryKey: ['route', LIST_ID] });
  });

  it('rejects when listId is null', async () => {
    const qc = makeClient();
    const { result } = renderHook(() => useOptimizeRoute(null), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await expect(
        result.current.mutateAsync({ lat: 1, lng: 2 }),
      ).rejects.toThrow('no_active_list');
    });
    expect(listClient.post).not.toHaveBeenCalled();
  });

  it('exposes error state on failure', async () => {
    (listClient.post as jest.Mock).mockRejectedValue(new Error('empty_list'));
    const qc = makeClient();
    const { result } = renderHook(() => useOptimizeRoute(LIST_ID), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await expect(
        result.current.mutateAsync({ lat: 48.86, lng: 2.38 }),
      ).rejects.toThrow('empty_list');
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
