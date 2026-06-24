// ratis_client/services/list-client.ts
import { createApiClient } from '@/services/api-client';
import { requireEnv } from '@/services/env';

// Lazy thunk : `requireEnv` throws on missing/empty — wrapping in a function
// defers the throw until first request, so a misconfigured bundle doesn't
// crash at module import time.
export const listClient = createApiClient(
  () => requireEnv('EXPO_PUBLIC_LIST_API_URL', process.env.EXPO_PUBLIC_LIST_API_URL),
);
