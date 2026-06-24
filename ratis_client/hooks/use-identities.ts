// ratis_client/hooks/use-identities.ts
//
// React Query hooks for the OAuth identity-management surface (H2 Phase 2):
//  - useIdentities      → GET    /account/identities
//  - useLinkProvider    → POST   /account/link-provider
//  - useUnlinkProvider  → DELETE /account/identities/{provider}
//
// Backed by `authService` (services/auth-service.ts). The mutations invalidate
// the identities query on success so the "Comptes liés" section re-renders
// with the fresh provider list.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { authService } from '@/services/auth-service';

export interface Identity {
  provider: string;
  email: string | null;
  created_at: string;
}

const IDENTITIES_KEY = ['account-identities'];

export function useIdentities() {
  return useQuery<Identity[]>({
    queryKey: IDENTITIES_KEY,
    queryFn: () => authService.listIdentities(),
  });
}

export function useLinkProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, token }: { provider: 'apple' | 'google'; token: string }) =>
      authService.linkProvider(provider, token),
    onSuccess: () => void qc.invalidateQueries({ queryKey: IDENTITIES_KEY }),
  });
}

export function useUnlinkProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (provider: 'apple' | 'google') => authService.unlinkProvider(provider),
    onSuccess: () => void qc.invalidateQueries({ queryKey: IDENTITIES_KEY }),
  });
}
