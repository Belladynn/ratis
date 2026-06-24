// ratis_client/app/_layout.tsx

// Side-effect imports — order matters: i18n then Sentry must initialise before
// any component renders. Both modules run their init as a side-effect of being
// imported (see services/sentry.ts, lib/i18n.ts).
import '@/lib/i18n';
import '@/services/sentry';

import React, { Component, ReactNode, useEffect } from 'react';
import { AppState, type AppStateStatus, View } from 'react-native';
import { Slot, useRouter, useSegments, type Href } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import * as Updates from 'expo-updates';
import * as Sentry from '@sentry/react-native';
import Constants from 'expo-constants';
import { logger } from '@/services/logger';
import { OTA_BUILD } from '@/services/build-info';
import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';
import 'react-native-reanimated';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useColorScheme } from '@/hooks/use-color-scheme';
import { useDesignSystemFonts } from '@/hooks/use-fonts';
import { AuthProvider } from '@/contexts/AuthContext';
import { useAuth } from '@/hooks/useAuth';
import { AppCrashScreen } from '@/components/AppCrashScreen';
import { processQueue, resetOrphanedUploads } from '@/services/scan-queue';
import { AchievementUnlockOverlay } from '@/components/achievements/unlock-overlay';
import { triggerSecretEvent } from '@/services/rewards-client';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, // 30s — adapté aux données gamification
      retry: 1,
    },
  },
});

void Promise.resolve(SplashScreen.preventAutoHideAsync()).catch(() => {});

export const unstable_settings = {
  anchor: '(tabs)',
};

export function AuthGate({ fontsReady }: { fontsReady: boolean }) {
  const auth = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const inAuthGroup = (segments[0] as string) === '(auth)';
  const ready = auth.status !== 'initializing' && fontsReady;

  useEffect(() => {
    if (ready) {
      void Promise.resolve(SplashScreen.hideAsync()).catch(() => {});
    }
  }, [ready]);

  useEffect(() => {
    if (!ready) return;

    if (auth.status === 'authenticated' && inAuthGroup) {
      router.replace('/(tabs)/scan' as Href);
    } else if (auth.status !== 'authenticated' && !inAuthGroup) {
      router.replace('/(auth)/login' as Href);
    }
  }, [ready, auth.status, segments, router, inAuthGroup]);

  if (!ready) {
    return <View style={{ flex: 1, backgroundColor: '#fff' }} />;
  }
  return <Slot />;
}

class ErrorBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    // Capture render-time crashes to Sentry — without this, the user sees
    // AppCrashScreen ("Une erreur inattendue s'est produite") but Sentry
    // sees nothing, so we have zero way to know what crashed. Lesson
    // 2026-04-26 alpha (cf KP-26).
    logger.error('app.error_boundary', error, {
      component_stack: info.componentStack ?? '<missing>',
    });
  }

  render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

export default function RootLayout() {
  const colorScheme = useColorScheme();
  // Inter weights 400-900 (design system PR2). Splash stays up until the
  // bundle is loaded — graceful fallback to system fonts if loading fails
  // so a font CDN outage doesn't brick the app.
  const [fontsLoaded, fontsError] = useDesignSystemFonts();
  const fontsReady = fontsLoaded || fontsError !== null;

  useEffect(() => {
    if (fontsError) {
      logger.warn('app.fonts_load_error', { message: fontsError.message });
      Sentry.captureException(fontsError, { tags: { phase: 'fonts.load' } });
    }
  }, [fontsError]);

  // One-shot boot log : confirms which JS bundle is actually running on the
  // device. Critical for OTA debugging — without this, "did the OTA apply?"
  // is unanswerable. Lesson 2026-04-26 alpha (cf KP-26).
  //
  // 2026-04-27 — promoted from logger.info (console-only) to
  // Sentry.captureMessage so the boot event reaches the dashboard. Without
  // this, "did the user pull the new bundle ?" is only answerable by
  // shipping a behaviour-changing OTA and watching its side-effects, which
  // is slower than just checking Sentry. The captured event also brings
  // along recent breadcrumbs (scan.queue.* from PR #134) so we can debug
  // upload-time URI handling without forcing a missing-file path.
  useEffect(() => {
    const bootData = {
      // Version trio — same as profil tab badge (services/build-info.ts).
      app_version: Constants.expoConfig?.version ?? 'unknown',
      native_build_version: Constants.nativeBuildVersion ?? 'unknown',
      ota_build: OTA_BUILD,
      runtime_version: Updates.runtimeVersion ?? 'unknown',
      update_id: Updates.updateId ?? 'embedded',
      channel: Updates.channel ?? 'unknown',
      is_embedded: Updates.isEmbeddedLaunch ?? null,
    };
    logger.info('app.boot', bootData);
    Sentry.captureMessage('app.boot', { level: 'info', extra: bootData });
  }, []);

  // Achievements V1 — secret event "app opened at 3am" (UTC). Fire-and-
  // forget at boot only ; the dispatcher is idempotent and rate-limited
  // server-side (10/h/user) so a re-mount can't spam the endpoint. We
  // intentionally check `getHours()` against 0..3 inclusive lower / exclusive
  // upper to match the typical "very late night" window.
  useEffect(() => {
    const h = new Date().getHours();
    if (h >= 0 && h < 4) {
      triggerSecretEvent('app_opened_at_3am').catch(() => {
        // Silent — secret event must never surface as an error to the user.
      });
    }
  }, []);

  // Scan upload queue — boot recovery + foreground re-trigger.
  //
  // Two failure modes diagnosed prod 2026-04-27 :
  //   (1) App killed mid-upload → entry stuck `status='uploading'` forever
  //       (`processQueue` only picks `queued`). Boot-time reset rewrites them
  //       back to `queued` so they get retried this run.
  //   (2) `processQueue` is single-flight; an entry enqueued during an
  //       in-flight pass would only resume on the next external trigger.
  //       Listen to AppState 'active' so a foreground transition kicks the
  //       queue — combined with the new while-loop drain inside
  //       `processQueue`, the queue always reaches zero.
  useEffect(() => {
    void resetOrphanedUploads()
      .then(() => processQueue())
      .catch((err) => {
        logger.error('scan.queue.boot_drain_error', err);
      });

    const onChange = (state: AppStateStatus) => {
      if (state === 'active') {
        processQueue().catch((err) => {
          logger.error('scan.queue.foreground_drain_error', err);
        });
      }
    };
    const sub = AppState.addEventListener('change', onChange);
    return () => {
      sub.remove();
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ErrorBoundary fallback={<AppCrashScreen />}>
          <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
            <AuthGate fontsReady={fontsReady} />
            {/* Mounted ONCE at root — listens to the achievement bus and
                renders the toast / celebration / bespoke overlay on top of
                whatever screen is active. */}
            <AchievementUnlockOverlay />
            <StatusBar style="auto" />
          </ThemeProvider>
        </ErrorBoundary>
      </AuthProvider>
    </QueryClientProvider>
  );
}
