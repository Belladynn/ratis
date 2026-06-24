// ratis_client/constants/Timeouts.ts

export const TIMEOUTS = {
  REFRESH_TOKEN: 15_000,
  WAIT_FOR_ONLINE: 15_000,
  DEFAULT_REQUEST: 30_000,
  UPLOAD: 60_000,
  UI_SLOW_FEEDBACK_MS: 3_000,
  UI_VERY_SLOW_FEEDBACK_MS: 8_000,
} as const;
