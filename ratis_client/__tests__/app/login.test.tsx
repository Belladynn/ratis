// ratis_client/__tests__/app/login.test.tsx

jest.mock("expo-secure-store");
jest.mock("expo-apple-authentication");
jest.mock("expo-crypto");
jest.mock("expo-localization", () => ({
  getCalendars: () => [{ timeZone: "Europe/Paris" }],
  getLocales: () => [{ languageCode: "fr" }],
}));
// scan-queue pulls in expo-task-manager at module load — AuthContext imports
// clearScanStorage from it, so mock the whole module.
jest.mock("@/services/scan-queue", () => ({
  clearScanStorage: jest.fn().mockResolvedValue(undefined),
}));

import React from "react";
import { render } from "@testing-library/react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import "@/lib/i18n";
import { AuthProvider } from "@/contexts/AuthContext";
import LoginScreen from "@/app/(auth)/login";
import { __setBaseUrl, __resetAuthState } from "@/services/api-client";
import { BASE_URL, defaultHandlers } from "@/tests/fixtures/msw-handlers";
import * as SecureStore from "expo-secure-store";
import * as Apple from "expo-apple-authentication";

const mockStore = SecureStore as unknown as { __reset: () => void };
const mockApple = Apple as unknown as {
  __setAvailable: (v: boolean) => void;
  __reset: () => void;
};
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

const Wrapped = () => (
  <QueryClientProvider client={new QueryClient()}>
    <AuthProvider>
      <LoginScreen />
    </AuthProvider>
  </QueryClientProvider>
);

describe("<LoginScreen />", () => {
  it("shows Google button always", async () => {
    const { findByTestId } = render(<Wrapped />);
    expect(await findByTestId("google-signin")).toBeTruthy();
  });

  it("shows Apple button when isAvailableAsync=true", async () => {
    mockApple.__setAvailable(true);
    const { findByTestId } = render(<Wrapped />);
    // Platform.OS in jest-expo defaults to ios, so Apple button should appear
    expect(await findByTestId("apple-signin")).toBeTruthy();
  });

  it("renders legal footer", async () => {
    const { findByTestId } = render(<Wrapped />);
    expect(await findByTestId("legal-cgu")).toBeTruthy();
  });
});
