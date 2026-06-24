// ratis_client/__tests__/contexts/AuthContext.test.tsx

jest.mock("expo-secure-store");
jest.mock("expo-apple-authentication");
jest.mock("expo-crypto");
jest.mock("expo-localization", () => ({
  getCalendars: () => [{ timeZone: "Europe/Paris" }],
  timezone: "Europe/Paris",
}));
// scan-queue pulls in expo-task-manager at module load; mock the whole module
// so AuthContext can import clearScanStorage without dragging native deps in.
jest.mock("@/services/scan-queue", () => ({
  clearScanStorage: jest.fn().mockResolvedValue(undefined),
}));

// @react-native-google-signin/google-signin is globally mocked in jest.setup.js.
// Drive the sign-in result via `global.__setNextGoogleResult({...})` if a
// specific test needs to (most AuthContext tests don't trigger Google sign-in
// directly).

import React from "react";
import { render, waitFor, act } from "@testing-library/react-native";
import { Text } from "react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { AuthProvider } from "@/contexts/AuthContext";
import { useAuth } from "@/hooks/useAuth";
import { tokenStorage } from "@/services/token-storage";
import { authEvents } from "@/services/auth-events";
import { __setBaseUrl, __resetAuthState } from "@/services/api-client";
import { clearScanStorage } from "@/services/scan-queue";
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
  __resetAuthState();
});
afterAll(() => server.close());

function Probe() {
  const a = useAuth();
  return <Text testID="status">{a.status}</Text>;
}

// AuthProvider now consumes useQueryClient() (cache purge on logout), so every
// render must supply a QueryClientProvider — exactly as app/_layout.tsx does.
function renderAuth(child: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>{child}</AuthProvider>
    </QueryClientProvider>
  );
}

describe("AuthContext — boot", () => {
  it("boots into unauthenticated when no tokens", async () => {
    const { getByTestId } = renderAuth(<Probe />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("unauthenticated"));
  });

  it("boots into authenticated when tokens exist and /auth/me succeeds", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });

    const { getByTestId } = renderAuth(<Probe />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("authenticated"));
  });

  it("boots into authenticated (optimistic) when /auth/me fails with a network error", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    server.use(
      http.get(`${BASE_URL}/auth/me`, () => HttpResponse.error())
    );

    const { getByTestId } = renderAuth(<Probe />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("authenticated"));
    // Tokens must be preserved — the session is still valid, only the
    // connectivity check failed.
    await expect(tokenStorage.get()).resolves.not.toBeNull();
  });

  it("boots into unauthenticated when /auth/me fails even with stored tokens", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    server.use(
      http.get(`${BASE_URL}/auth/me`, () =>
        HttpResponse.json({ detail: "user_deleted" }, { status: 401 })
      ),
      http.post(`${BASE_URL}/auth/refresh`, () =>
        HttpResponse.json({ detail: "invalid_refresh_token" }, { status: 401 })
      )
    );

    const { getByTestId } = renderAuth(<Probe />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("unauthenticated"));
  });
});

describe("AuthContext — force_logout event", () => {
  it("transitions authenticated → unauthenticated when authEvents.emitForceLogout", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });

    const { getByTestId } = renderAuth(<Probe />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("authenticated"));

    act(() => {
      authEvents.emitForceLogout();
    });

    await waitFor(() => expect(getByTestId("status").props.children).toBe("unauthenticated"));
    await expect(tokenStorage.get()).resolves.toBeNull();
  });
});

describe("AuthContext — data isolation on logout", () => {
  function renderWithQuery(client: QueryClient, child: React.ReactNode) {
    return render(
      <QueryClientProvider client={client}>
        <AuthProvider>{child}</AuthProvider>
      </QueryClientProvider>
    );
  }

  it("clears the React Query cache on signOut (no cross-account leak)", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    const client = new QueryClient();
    client.setQueryData(["account-stats"], { balance: 9999 });

    function Trigger() {
      const a = useAuth();
      return (
        <Text testID="status" onPress={() => void a.signOut()}>
          {a.status}
        </Text>
      );
    }
    const { getByTestId } = renderWithQuery(client, <Trigger />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("authenticated"));

    await act(async () => {
      getByTestId("status").props.onPress();
    });

    await waitFor(() => expect(getByTestId("status").props.children).toBe("unauthenticated"));
    expect(client.getQueryData(["account-stats"])).toBeUndefined();
  });

  it("clears the React Query cache on force-logout", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    const client = new QueryClient();
    client.setQueryData(["account-stats"], { balance: 9999 });

    const { getByTestId } = renderWithQuery(client, <Probe />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("authenticated"));

    act(() => {
      authEvents.emitForceLogout();
    });

    await waitFor(() => expect(getByTestId("status").props.children).toBe("unauthenticated"));
    expect(client.getQueryData(["account-stats"])).toBeUndefined();
  });

  it("purges scan storage on signOut", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    const client = new QueryClient();

    function Trigger() {
      const a = useAuth();
      return (
        <Text testID="status" onPress={() => void a.signOut()}>
          {a.status}
        </Text>
      );
    }
    const { getByTestId } = renderWithQuery(client, <Trigger />);
    await waitFor(() => expect(getByTestId("status").props.children).toBe("authenticated"));

    await act(async () => {
      getByTestId("status").props.onPress();
    });

    await waitFor(() => expect(getByTestId("status").props.children).toBe("unauthenticated"));
    expect(clearScanStorage).toHaveBeenCalled();
  });
});

describe("useAuth", () => {
  it("throws if used outside AuthProvider", () => {
    function Bad() {
      useAuth();
      return null;
    }
    const origErr = console.error;
    console.error = jest.fn();
    try {
      expect(() => render(<Bad />)).toThrow("useAuth must be inside AuthProvider");
    } finally {
      console.error = origErr;
    }
  });
});
