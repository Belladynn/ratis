// ratis_client/__tests__/services/auth-service.test.ts

jest.mock("expo-secure-store");
jest.mock("expo-apple-authentication");
jest.mock("expo-crypto");
jest.mock("expo-localization", () => ({
  getCalendars: () => [{ timeZone: "Europe/Paris" }],
  timezone: "Europe/Paris",
}));
jest.mock("expo-constants", () => ({
  expoConfig: {
    extra: {
      googleClientIdIos:     "test-ios-client-id",
      googleClientIdAndroid: "test-android-client-id",
    },
  },
}));

import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { authService } from "@/services/auth-service";
import { tokenStorage } from "@/services/token-storage";
import { BASE_URL, defaultHandlers } from "@/tests/fixtures/msw-handlers";
import { __setBaseUrl, __resetAuthState } from "@/services/api-client";
import * as Apple from "expo-apple-authentication";
import * as SecureStore from "expo-secure-store";

const mockApple = Apple as unknown as {
  __setAvailable: (v: boolean) => void;
  __setNextResult: (r: unknown) => void;
  __reset: () => void;
};
const mockStore = SecureStore as unknown as { __reset: () => void };

const server = setupServer(...defaultHandlers);

beforeAll(() => {
  __setBaseUrl(BASE_URL);
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  mockStore.__reset();
  mockApple.__reset();
  __resetAuthState();
});
afterAll(() => server.close());

describe("authService.signInWithApple", () => {
  it("fetches idToken, exchanges for tokens, stores them, loads /auth/me", async () => {
    const user = await authService.signInWithApple();
    expect(user.id).toBe("u-1");

    await expect(tokenStorage.getAccess()).resolves.toBe("access.jwt.1");
    await expect(tokenStorage.getRefresh()).resolves.toBe("refresh.jwt.1");
  });

  it("throws CANCELLED AuthError when user cancels the prompt", async () => {
    mockApple.__setNextResult(new Error("ERR_CANCELED"));

    await expect(authService.signInWithApple()).rejects.toMatchObject({
      errorClass: "CANCELLED",
    });
  });

  it("throws VALIDATION_ERROR when backend rejects the Apple token", async () => {
    server.use(
      http.post(`${BASE_URL}/auth/oauth`, () =>
        HttpResponse.json({ detail: "invalid_apple_token" }, { status: 401 })
      )
    );

    await expect(authService.signInWithApple()).rejects.toMatchObject({
      code: "invalid_apple_token",
      errorClass: "VALIDATION_ERROR",
    });
  });

  it("includes timezone in the /auth/oauth request", async () => {
    let receivedBody: any = null;
    server.use(
      http.post(`${BASE_URL}/auth/oauth`, async ({ request }) => {
        receivedBody = await request.json();
        return HttpResponse.json({
          access_token: "a",
          refresh_token: "r",
          expires_in: 900,
          token_type: "bearer",
        });
      })
    );

    await authService.signInWithApple();
    expect(receivedBody.timezone).toBe("Europe/Paris");
    expect(receivedBody.provider).toBe("apple");
  });
});

describe("authService.signOut", () => {
  it("calls /account/logout and clears token storage", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });

    let logoutCalled = false;
    server.use(
      http.post(`${BASE_URL}/account/logout`, () => {
        logoutCalled = true;
        return HttpResponse.json({ ok: true });
      })
    );

    await authService.signOut();
    expect(logoutCalled).toBe(true);
    await expect(tokenStorage.get()).resolves.toBeNull();
  });

  it("clears tokens even if /account/logout fails (best-effort)", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });

    server.use(
      http.post(`${BASE_URL}/account/logout`, () =>
        HttpResponse.json({ detail: "internal" }, { status: 500 })
      )
    );

    await expect(authService.signOut()).resolves.toBeUndefined();
    await expect(tokenStorage.get()).resolves.toBeNull();
  });

  it("clears tokens even on network error", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });

    server.use(
      http.post(`${BASE_URL}/account/logout`, () => HttpResponse.error())
    );

    await expect(authService.signOut()).resolves.toBeUndefined();
    await expect(tokenStorage.get()).resolves.toBeNull();
  });
});

describe("authService.exchangeOAuthToken (Google)", () => {
  // The Google OAuth popup flow itself (request/promptAsync) is now owned by
  // AuthContext via `Google.useAuthRequest` hook — see the matching test in
  // __tests__/contexts/AuthContext.test.tsx for the full flow.
  // These tests cover only the backend exchange happening AFTER the id_token
  // is obtained from Google (regardless of how it was obtained).

  it("exchanges idToken with backend, stores JWT, returns user", async () => {
    const user = await authService.exchangeOAuthToken("google", "fake.google.jwt");
    expect(user.id).toBe("u-1");
  });

  it("throws VALIDATION_ERROR when backend rejects Google token", async () => {
    server.use(
      http.post(`${BASE_URL}/auth/oauth`, () =>
        HttpResponse.json({ detail: "invalid_google_token" }, { status: 401 })
      )
    );

    await expect(authService.exchangeOAuthToken("google", "bad")).rejects.toMatchObject({
      code: "invalid_google_token",
      errorClass: "VALIDATION_ERROR",
    });
  });
});
