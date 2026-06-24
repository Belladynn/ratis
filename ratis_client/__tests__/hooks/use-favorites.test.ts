import { renderHook, waitFor, act } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useFavorites, useIsFavorite, useToggleFavorite } from '@/hooks/use-favorites';
import { productClient } from '@/services/product-client';

jest.mock('@/services/product-client', () => ({
  productClient: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
}));

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

const EAN = '3428270000019';
const FAV = {
  ean: EAN,
  name: 'Lait demi-écrémé 1L',
  photo_url_small: null,
  created_at: '2026-04-20T12:00:00+00:00',
};

describe('useFavorites', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
    (productClient.post as jest.Mock).mockReset();
    (productClient.delete as jest.Mock).mockReset();
  });

  it('fetches the favorites list', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [FAV] });
    const { result } = renderHook(() => useFavorites(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(productClient.get).toHaveBeenCalledWith('/product/favorites');
    expect(result.current.data).toEqual({ items: [FAV] });
  });

  it('returns empty items for a new user', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useFavorites(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items).toEqual([]);
  });

  it('exposes error state on failure', async () => {
    (productClient.get as jest.Mock).mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useFavorites(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useToggleFavorite', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
    (productClient.post as jest.Mock).mockReset();
    (productClient.delete as jest.Mock).mockReset();
  });

  it('POSTs when favorited=true', async () => {
    (productClient.post as jest.Mock).mockResolvedValue({ favorited: true });
    const { result } = renderHook(() => useToggleFavorite(), { wrapper: makeWrapper() });
    await act(async () => {
      await result.current.mutateAsync({ ean: EAN, favorited: true });
    });
    expect(productClient.post).toHaveBeenCalledWith(`/product/${EAN}/favorite`);
    expect(productClient.delete).not.toHaveBeenCalled();
  });

  it('DELETEs when favorited=false', async () => {
    (productClient.delete as jest.Mock).mockResolvedValue({ favorited: false });
    const { result } = renderHook(() => useToggleFavorite(), { wrapper: makeWrapper() });
    await act(async () => {
      await result.current.mutateAsync({ ean: EAN, favorited: false });
    });
    expect(productClient.delete).toHaveBeenCalledWith(`/product/${EAN}/favorite`);
    expect(productClient.post).not.toHaveBeenCalled();
  });
});

describe('useIsFavorite', () => {
  beforeEach(() => {
    (productClient.get as jest.Mock).mockReset();
  });

  it('returns true when ean is in the favorites list', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [FAV] });
    const { result } = renderHook(() => useIsFavorite(EAN), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('returns false when ean is missing from the list', async () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [FAV] });
    const { result } = renderHook(() => useIsFavorite('9999999999999'), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(productClient.get).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });

  it('returns false when ean is undefined', () => {
    (productClient.get as jest.Mock).mockResolvedValue({ items: [FAV] });
    const { result } = renderHook(() => useIsFavorite(undefined), {
      wrapper: makeWrapper(),
    });
    expect(result.current).toBe(false);
  });
});
