// ratis_client/contexts/AuthContext.tsx

import React, { createContext, useCallback, useEffect, useReducer } from "react";
import {
  GoogleSignin,
  isSuccessResponse,
  isCancelledResponse,
  statusCodes,
} from "@react-native-google-signin/google-signin";
import Constants from "expo-constants";
import { useQueryClient } from "@tanstack/react-query";
import { authReducer, initialState } from "@/contexts/authReducer";
import { authService } from "@/services/auth-service";
import { tokenStorage } from "@/services/token-storage";
import { authEvents } from "@/services/auth-events";
import { apiClient } from "@/services/api-client";
import { clearScanStorage } from "@/services/scan-queue";
import { logger } from "@/services/logger";
import { AuthError, AuthState, User } from "@/types/auth";

// Configure Google Sign-In once at module load.
// - webClientId : audience of the id_token returned (validated by backend
//   via GOOGLE_CLIENT_ID env var). REQUIRED.
// - iosClientId : iOS-only, optional but recommended for iOS native flow.
// On Android, the lib uses Google Play Services Sign-In SDK — no web browser,
// no deep linking, no redirect URI configuration. Google Console requires the
// SHA-1 of the signing key to be registered for the Android client ID.
// Cf KP-26 in KNOWN_PROBLEMS.md (alpha 2026-04-25 OAuth debug — switched away
// from expo-auth-session/providers/google because it relied on a deep-link
// redirect that crashed into expo-router as "unmatched route").
const extra = Constants.expoConfig?.extra as
  | { googleClientIdIos?: string; googleClientIdWeb?: string }
  | undefined;

const GOOGLE_CONFIG = {
  webClientId: extra?.googleClientIdWeb ?? "",
  iosClientId: extra?.googleClientIdIos,
  scopes: ["openid", "email", "profile"],
};

function configureGoogleSignIn(): void {
  // Idempotent — safe to call multiple times. Re-invoked defensively before
  // each signIn() because GoogleSignin's native state can be lost if the OS
  // freezes the app process (deep sleep on Android), and a fresh signIn()
  // on a stale native module surfaces as a generic 'unknown_error'. The
  // observable symptom of that race was: login worked on a fresh launch,
  // then started failing with "Erreur inattendue" after the phone slept.
  if (!GOOGLE_CONFIG.webClientId) return;
  GoogleSignin.configure(GOOGLE_CONFIG);
}

configureGoogleSignIn();

/**
 * Run the native Google Play Services Sign-In flow and return the raw
 * `idToken`. Shared between the initial sign-in flow (`signIn`) and the
 * account-linking flow (`getProviderToken`) — the configure/hasPlayServices
 * dance and the cancellation/missing-token translation live in one place.
 */
async function getGoogleIdToken(): Promise<string> {
  // Re-run configure each time — defensive against state loss after OS
  // process freeze (cf. comment in configureGoogleSignIn).
  configureGoogleSignIn();
  await GoogleSignin.hasPlayServices({ showPlayServicesUpdateDialog: true });
  const response = await GoogleSignin.signIn();
  if (isCancelledResponse(response)) {
    throw new AuthError("cancelled", "CANCELLED");
  }
  if (!isSuccessResponse(response) || !response.data.idToken) {
    throw new AuthError("google_missing_token", "VALIDATION_ERROR");
  }
  return response.data.idToken;
}

export type AuthContextValue = AuthState & {
  signIn: (provider: "apple" | "google") => Promise<void>;
  signOut: () => Promise<void>;
  /**
   * Run the native provider flow and resolve with the raw provider id_token,
   * WITHOUT exchanging it for a Ratis session. Used by the account-linking
   * screen, which POSTs the token to /account/link-provider instead.
   */
  getProviderToken: (provider: "apple" | "google") => Promise<string>;
  /** Dev-only bypass — no-op in production builds */
  devSignIn: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(authReducer, initialState);
  // AuthProvider is mounted under QueryClientProvider (app/_layout.tsx) so this
  // resolves the same client used by every screen's React Query hooks.
  const queryClient = useQueryClient();

  // Wipe every trace of the previous account from the device. On a shared
  // device, leaving these in place would let the next user see the prior
  // user's balance/stats/history (cache) or have a queued scan attributed to
  // the wrong account (scan storage). Called on both signOut and force-logout.
  const purgeAccountData = useCallback(async () => {
    queryClient.clear();
    await clearScanStorage().catch((err) => {
      logger.warn("auth.logout.scan_storage_clear_failed", {
        message: err instanceof Error ? err.message : String(err),
      });
    });
  }, [queryClient]);

  // Boot
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const tokens = await tokenStorage.get();
      if (!tokens) {
        if (!cancelled) dispatch({ type: "BOOT_FAIL" });
        return;
      }
      try {
        const user = await apiClient.get<User>("/auth/me");
        if (!cancelled) dispatch({ type: "BOOT_SUCCESS", user });
      } catch (err) {
        // Distinguish a transport failure (no connectivity, timeout) from a
        // genuine auth rejection. On a network error the stored token is most
        // likely still valid — ejecting the user here would lock them out of
        // the app simply for opening it offline. Keep the session optimistically
        // and let React Query refetch /auth/me once connectivity returns.
        const isNetworkError =
          err instanceof AuthError &&
          (err.errorClass === "NETWORK_ERROR" || err.errorClass === "TIMEOUT");
        if (isNetworkError) {
          if (!cancelled) dispatch({ type: "BOOT_OFFLINE" });
          return;
        }
        // A real auth error (401/403) — the token is invalid, clear it.
        await tokenStorage.clear();
        if (!cancelled) dispatch({ type: "BOOT_FAIL" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Force logout subscription
  useEffect(() => {
    return authEvents.onForceLogout(() => {
      void purgeAccountData();
      tokenStorage.clear().finally(() => {
        dispatch({ type: "FORCE_LOGOUT" });
      });
    });
  }, [purgeAccountData]);

  const signIn = useCallback(async (provider: "apple" | "google") => {
    dispatch({ type: "SIGNIN_START", provider });
    try {
      let user: User;
      if (provider === "apple") {
        user = await authService.signInWithApple();
      } else {
        // Google : native flow via Google Play Services SDK.
        const idToken = await getGoogleIdToken();
        user = await authService.exchangeOAuthToken("google", idToken);
      }
      dispatch({ type: "SIGNIN_SUCCESS", user });
    } catch (err) {
      // Translate native Google errors to AuthError
      const code = (err as { code?: string })?.code;
      if (code === statusCodes.SIGN_IN_CANCELLED) {
        dispatch({
          type: "SIGNIN_FAIL",
          error: new AuthError("cancelled", "CANCELLED"),
        });
        return;
      }
      if (code === statusCodes.PLAY_SERVICES_NOT_AVAILABLE) {
        dispatch({
          type: "SIGNIN_FAIL",
          error: new AuthError("google_play_unavailable", "NETWORK_ERROR"),
        });
        return;
      }
      // Capture the real native error to Sentry — without this, every
      // unmapped failure surfaces as a generic "Erreur inattendue" with no
      // stack, making post-deploy debug impossible. The error object from
      // GoogleSignin includes `code` + `message` (e.g. DEVELOPER_ERROR,
      // SIGN_IN_REQUIRED, NETWORK_ERROR) which are critical to triage.
      if (!(err instanceof AuthError)) {
        logger.error("auth.signin.native_error", err, { provider, code });
      }
      const authErr =
        err instanceof AuthError ? err : new AuthError("unknown_error", "SERVER_ERROR");
      dispatch({ type: "SIGNIN_FAIL", error: authErr });
    }
  }, []);

  const signOut = useCallback(async () => {
    await authService.signOut();
    // Best-effort Google sign-out — ignore failures (user may not be signed in).
    GoogleSignin.signOut().catch(() => undefined);
    // Purge cached account data + local scan storage before flipping state so
    // a shared device never surfaces the previous account's data.
    await purgeAccountData();
    dispatch({ type: "SIGNOUT" });
  }, [purgeAccountData]);

  // Run the native provider flow and return the raw id_token without touching
  // the session state — the account-linking screen owns the backend call.
  const getProviderToken = useCallback(
    async (provider: "apple" | "google"): Promise<string> => {
      if (provider === "apple") {
        return authService.getAppleIdentityToken();
      }
      return getGoogleIdToken();
    },
    [],
  );

  const devSignIn = useCallback(() => {
    if (!__DEV__) return;
    dispatch({
      type: "SIGNIN_SUCCESS",
      user: {
        id:               "dev-user",
        email:            "dev@ratis.app",
        display_name:     "Dev User",
        avatar_url:       null,
        account_type:     "oauth",
        timezone:         "Europe/Paris",
        current_level_id: null,
      },
    });
  }, []);

  return (
    <AuthContext.Provider
      value={{ ...state, signIn, signOut, getProviderToken, devSignIn }}
    >
      {children}
    </AuthContext.Provider>
  );
}
