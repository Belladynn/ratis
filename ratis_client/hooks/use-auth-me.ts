// ratis_client/hooks/use-auth-me.ts
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/services/api-client';

export interface AuthMe {
  id: string;
  email: string;
  account_type: string;
  display_name: string | null;
  avatar_url: string | null;
  timezone: string;
  current_level_id: string | null;
  /**
   * Public, non-PII identifier the user can share with support
   * (e.g. on Twitter). Format `RTS-XXXXXX` — 6 chars from a 32-char
   * alphabet (no I/O/0/1). Generated backend-side (PR #234).
   */
  support_id: string;
  created_at: string;
  updated_at: string;
}

export function useAuthMe() {
  return useQuery<AuthMe>({
    queryKey: ['auth-me'],
    queryFn: () => apiClient.get<AuthMe>('/auth/me'),
    staleTime: 5 * 60_000, // 5 min
  });
}
