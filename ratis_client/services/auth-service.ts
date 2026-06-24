// ratis_client/services/auth-service.ts

import * as Apple from "expo-apple-authentication";
import * as Crypto from "expo-crypto";
import { getCalendars } from "expo-localization";
import { apiClient } from "@/services/api-client";
import { tokenStorage } from "@/services/token-storage";
import { AuthError, User } from "@/types/auth";
import { logger } from "@/services/logger";

function getTimezone(): string {
  const cal = getCalendars()[0];
  return cal?.timeZone ?? "Europe/Paris";
}

async function exchangeOAuth(provider: "apple" | "google", token: string): Promise<User> {
  const timezone = getTimezone();

  let tokens: { access_token: string; refresh_token: string; expires_in: number };
  try {
    tokens = await apiClient.post<{
      access_token: string;
      refresh_token: string;
      expires_in: number;
    }>("/auth/oauth", { provider, token, timezone });
  } catch (err) {
    // A 401 from /auth/oauth means the provider token itself was rejected —
    // it is a validation concern, not an expired app session.
    if (err instanceof AuthError && err.httpStatus === 401) {
      throw new AuthError(err.code, "VALIDATION_ERROR", err.httpStatus, err.details);
    }
    throw err;
  }

  await tokenStorage.set({
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
    expiresAt: Date.now() + tokens.expires_in * 1000,
  });

  const user = await apiClient.get<User>("/auth/me");
  logger.info("auth.signin.success", { provider, user_id: user.id });
  return user;
}

/**
 * Run the native Apple Sign-In prompt and return the raw `identityToken`.
 *
 * Shared between the initial sign-in flow (`signInWithApple`) and the
 * account-linking flow (`AuthContext.getProviderToken`) so the nonce handling
 * and cancellation/error translation live in exactly one place.
 */
async function getAppleIdentityToken(): Promise<string> {
  const rawNonce = Crypto.randomUUID();
  const hashedNonce = await Crypto.digestStringAsync(
    Crypto.CryptoDigestAlgorithm.SHA256,
    rawNonce
  );

  let credential: { identityToken: string | null };
  try {
    credential = await Apple.signInAsync({
      requestedScopes: [
        Apple.AppleAuthenticationScope.EMAIL,
        Apple.AppleAuthenticationScope.FULL_NAME,
      ],
      nonce: hashedNonce,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("ERR_CANCELED") || msg.includes("canceled")) {
      throw new AuthError("cancelled", "CANCELLED");
    }
    logger.warn("auth.apple.prompt_error", { message: msg });
    throw new AuthError("apple_unavailable", "VALIDATION_ERROR");
  }

  if (!credential.identityToken) {
    throw new AuthError("apple_missing_token", "VALIDATION_ERROR");
  }

  return credential.identityToken;
}

export const authService = {
  /** Run the native Apple Sign-In prompt; resolve with the raw id_token. */
  getAppleIdentityToken,

  async signInWithApple(): Promise<User> {
    const identityToken = await getAppleIdentityToken();
    return exchangeOAuth("apple", identityToken);
  },

  /**
   * Exchange an OAuth provider's id_token for Ratis JWT tokens.
   *
   * The provider-side flow (Google native SDK via @react-native-google-signin,
   * Apple native UI) is owned by the caller — typically `AuthContext`. This
   * service only handles the backend exchange : POST /auth/oauth → store JWT
   * → fetch user. Cf KP-26 in KNOWN_PROBLEMS.md (alpha 2026-04-25 OAuth debug).
   */
  async exchangeOAuthToken(
    provider: "apple" | "google",
    idToken: string
  ): Promise<User> {
    return exchangeOAuth(provider, idToken);
  },

  async signOut(): Promise<void> {
    // Best-effort backend notification — never block on failure
    try {
      await apiClient.post("/account/logout", {});
    } catch (err) {
      logger.warn("auth.logout.backend_failed", { code: (err as AuthError)?.code });
    }
    await tokenStorage.clear();
    logger.info("auth.logout.success");
  },

  /**
   * List the OAuth identities (provider mappings) linked to the current
   * account. Backed by GET /account/identities (H2 Phase 2).
   */
  async listIdentities(): Promise<
    { provider: string; email: string | null; created_at: string }[]
  > {
    return apiClient.get<{ provider: string; email: string | null; created_at: string }[]>(
      "/account/identities"
    );
  },

  /**
   * Link an additional OAuth provider to the current account by POSTing the
   * provider's raw id_token to /account/link-provider (H2 Phase 2). The token
   * is obtained from the native provider flow — see AuthContext.getProviderToken.
   */
  async linkProvider(provider: "apple" | "google", token: string): Promise<void> {
    await apiClient.post("/account/link-provider", { provider, token });
  },

  /**
   * Unlink an OAuth provider from the current account via
   * DELETE /account/identities/{provider} (H2 Phase 2). The backend rejects
   * unlinking the last remaining identity (cannot_unlink_last_identity).
   */
  async unlinkProvider(provider: "apple" | "google"): Promise<void> {
    await apiClient.delete(`/account/identities/${provider}`);
  },
};
