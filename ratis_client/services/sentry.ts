// ratis_client/services/sentry.ts
// Sentry-compatible error tracking initialisation — runs as a side-effect on
// first import so any caller only needs `import '@/services/sentry';` at the
// top of their entry point. This keeps _layout.tsx free of mid-file statements
// (ESLint import/first).
//
// Backend cible 2026-05-31+ : **GlitchTip self-hosted** (cf
// `docs/arch/ARCH_incident_management.md`, DA-47). GlitchTip implémente le
// protocole Sentry SDK nativement → ce module reste compatible sans changement
// de code. Le DSN à injecter (via `Constants.expoConfig?.extra?.sentryDsn`)
// vient du Keychain account `ops-glitchtip-dsn-ratis-mobile`, propagé au build
// EAS via secrets EAS ou `eas.json` env.

import * as Sentry from '@sentry/react-native';
import Constants from 'expo-constants';

export function initSentry(): void {
  const dsn = Constants.expoConfig?.extra?.sentryDsn as string | undefined;
  if (!dsn) return; // no-op in CI / envs without DSN

  Sentry.init({
    dsn,
    debug: false,
    sendDefaultPii: false,    // RGPD — never send emails, IPs or user identity
    enableNativeNagger: false, // silence "native module not loaded" warning in Expo Go
  });
}

// Auto-init on import. Safe to call multiple times (Sentry.init is idempotent)
// and a no-op when no DSN is configured (dev / CI / ejected builds).
initSentry();
