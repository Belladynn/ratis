// ratis_client/services/product-client.ts
import { createApiClient } from '@/services/api-client';
import { requireEnv } from '@/services/env';

// Lazy thunk — see list-client.ts for rationale.
export const productClient = createApiClient(
  () => requireEnv('EXPO_PUBLIC_PRODUCT_API_URL', process.env.EXPO_PUBLIC_PRODUCT_API_URL),
);
