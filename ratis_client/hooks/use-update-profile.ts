// ratis_client/hooks/use-update-profile.ts
// Mutation hook for PATCH /account/profile — updates display_name, timezone
// and/or avatar_url. Only keys provided in the input are sent (partial patch
// semantics expected by the backend UserUpdate Pydantic schema).

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/services/api-client';
import type { AuthMe } from '@/hooks/use-auth-me';

export interface UpdateProfileInput {
  display_name?: string;
  timezone?: string;
  avatar_url?: string | null;
}

/**
 * Drop keys whose value is strictly `undefined` so the backend treats them as
 * "not provided" (vs `null` which would be an explicit reset for nullable
 * fields like avatar_url). See Pydantic `model_fields_set` semantics.
 */
function stripUndefined(input: UpdateProfileInput): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (input.display_name !== undefined) out.display_name = input.display_name;
  if (input.timezone !== undefined) out.timezone = input.timezone;
  if (input.avatar_url !== undefined) out.avatar_url = input.avatar_url;
  return out;
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation<AuthMe, Error, UpdateProfileInput>({
    mutationFn: (input) => apiClient.patch<AuthMe>('/account/profile', stripUndefined(input)),
    onSuccess: () => {
      // The Profil screen + any other consumer of useAuthMe will re-fetch
      // fresh user data on next render.
      void qc.invalidateQueries({ queryKey: ['auth-me'] });
    },
  });
}
