// ratis_client/__tests__/integration/auth-flow.integration.test.tsx

jest.mock("expo-secure-store");
jest.mock("expo-apple-authentication");
jest.mock("expo-crypto");
jest.mock("expo-network");
jest.mock("expo-localization", () => ({
  getCalendars: () => [{ timeZone: "Europe/Paris" }],
  getLocales: () => [{ languageCode: "fr" }],
}));
jest.mock("expo-constants", () => ({
  expoConfig: {
    extra: {
      googleClientIdIos:     "test-ios-client-id",
      googleClientIdAndroid: "test-android-client-id",
    },
  },
}));

jest.mock("@/services/scan-queue", () => ({
  clearScanStorage: jest.fn().mockResolvedValue(undefined),
}));

import React from "react";
import { render, fireEvent, waitFor, act } from "@testing-library/react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { AuthProvider } from "@/contexts/AuthContext";
import LoginScreen from "@/app/(auth)/login";
import { __setBaseUrl, __resetAuthState } from "@/services/api-client";
import { BASE_URL, defaultHandlers } from "@/tests/fixtures/msw-handlers";
import { tokenStorage } from "@/services/token-storage";
import { authEvents } from "@/services/auth-events";
import * as SecureStore from "expo-secure-store";
import * as Apple from "expo-apple-authentication";

const mockStore = SecureStore as unknown as { __reset: () => void };
const mockApple = Apple as unknown as { __setNextResult: (r: unknown) => void; __reset: () => void };

// Globally mocked in jest.setup.js — drive Google Sign-In result via these helpers.
// Shape: { type: 'success', data: { idToken: string } } | { type: 'cancelled', data: null } | Error
declare const __setNextGoogleResult: (r: unknown) => void;
declare const __resetGoogleMock: () => void;

const server = setupServer(...defaultHandlers);

beforeAll(() => {
  __setBaseUrl(BASE_URL);
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  mockStore.__reset();
  mockApple.__reset();
  __resetGoogleMock();
  __resetAuthState();
});
afterAll(() => server.close());

const Wrapped = () => (
  <QueryClientProvider client={new QueryClient()}>
    <AuthProvider>
      <LoginScreen />
    </AuthProvider>
  </QueryClientProvider>
);

describe("Auth flow integration", () => {
  it("user signs in with Google → tokens are stored", async () => {
    __setNextGoogleResult({ type: "success", data: { idToken: "fake.google.jwt" } });

    const { getByTestId } = render(<Wrapped />);
    fireEvent.press(getByTestId("google-signin"));

    await waitFor(async () => {
      await expect(tokenStorage.getAccess()).resolves.toBe("access.jwt.1");
    });
    await expect(tokenStorage.getRefresh()).resolves.toBe("refresh.jwt.1");
  });

  it("user cancels Google OAuth → stays on login without error banner", async () => {
    __setNextGoogleResult({ type: "cancelled", data: null });

    const { getByTestId, queryByTestId } = render(<Wrapped />);
    fireEvent.press(getByTestId("google-signin"));

    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });

    expect(queryByTestId("error-banner")).toBeNull();
    await expect(tokenStorage.get()).resolves.toBeNull();
  });

  it("backend 401 on /auth/oauth shows error banner", async () => {
    __setNextGoogleResult({ type: "success", data: { idToken: "bad" } });
    server.use(
      http.post(`${BASE_URL}/auth/oauth`, () =>
        HttpResponse.json({ detail: "invalid_google_token" }, { status: 401 })
      )
    );

    const { getByTestId } = render(<Wrapped />);
    fireEvent.press(getByTestId("google-signin"));

    await waitFor(() => {
      expect(getByTestId("error-banner")).toBeTruthy();
    });
    await expect(tokenStorage.get()).resolves.toBeNull();
  });

  it("force_logout event clears tokens and triggers AuthProvider state update", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });

    render(<Wrapped />);

    // Wait for boot to complete (authenticated)
    await waitFor(async () => {
      const tokens = await tokenStorage.get();
      expect(tokens).not.toBeNull();
    });

    act(() => { authEvents.emitForceLogout(); });

    await waitFor(async () => {
      await expect(tokenStorage.get()).resolves.toBeNull();
    });
  });
});
