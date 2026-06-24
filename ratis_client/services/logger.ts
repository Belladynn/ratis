// ratis_client/services/logger.ts

import * as Sentry from '@sentry/react-native';

const PII_KEYS = [
  "access_token",
  "refresh_token",
  "token",
  "idToken",
  "email",
  "display_name",
  "password",
] as const;

function sanitize(data?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!data) return data;
  const clean: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if ((PII_KEYS as readonly string[]).includes(k)) continue;
    clean[k] = v;
  }
  return clean;
}

export const logger = {
  info(event: string, data?: Record<string, unknown>) {
    console.log(`[INFO] ${event}`, sanitize(data));
  },
  warn(event: string, data?: Record<string, unknown>) {
    console.warn(`[WARN] ${event}`, sanitize(data));
  },
  error(event: string, err: unknown, data?: Record<string, unknown>) {
    console.error(`[ERROR] ${event}`, err, sanitize(data));
    Sentry.captureException(
      err instanceof Error ? err : new Error(String(err)),
      { extra: sanitize(data) as Record<string, unknown> },
    );
  },
};

export const __internal__ = { sanitize };
