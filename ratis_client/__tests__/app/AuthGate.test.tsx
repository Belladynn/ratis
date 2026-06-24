// ratis_client/__tests__/app/AuthGate.test.tsx

jest.mock("expo-secure-store");
jest.mock("expo-splash-screen", () => ({
  preventAutoHideAsync: jest.fn(),
  hideAsync: jest.fn(),
}));

const mockReplace = jest.fn();
const mockSegments: string[] = ["(tabs)", "scan"];

jest.mock("expo-router", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSegments: () => mockSegments,
  Slot: () => null,
}));
// scan-queue drags in expo-task-manager at module load — mock the whole module
// (AuthContext imports clearScanStorage from it).
jest.mock("@/services/scan-queue", () => ({
  clearScanStorage: jest.fn().mockResolvedValue(undefined),
}));

import React from "react";
import { render, waitFor } from "@testing-library/react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { AuthProvider } from "@/contexts/AuthContext";
import { AuthGate } from "@/app/_layout";
import { tokenStorage } from "@/services/token-storage";
import { __setBaseUrl, __resetAuthState } from "@/services/api-client";
import { BASE_URL, defaultHandlers } from "@/tests/fixtures/msw-handlers";
import * as SecureStore from "expo-secure-store";

const mockStore = SecureStore as unknown as { __reset: () => void };
const server = setupServer(...defaultHandlers);

beforeAll(() => {
  __setBaseUrl(BASE_URL);
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  mockStore.__reset();
  mockReplace.mockClear();
  __resetAuthState();
  // Reset segments to tabs for next test
  mockSegments.length = 0;
  mockSegments.push("(tabs)", "scan");
});
afterAll(() => server.close());

describe("<AuthGate fontsReady={true} />", () => {
  it("redirects unauthenticated user from protected route to login", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <AuthGate fontsReady={true} />
        </AuthProvider>
      </QueryClientProvider>
    );
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/(auth)/login");
    });
  });

  it("redirects authenticated user from /(auth) to /(tabs)/scan", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    mockSegments.length = 0;
    mockSegments.push("(auth)", "login");

    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <AuthGate fontsReady={true} />
        </AuthProvider>
      </QueryClientProvider>
    );
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/(tabs)/scan");
    });
  });
});
