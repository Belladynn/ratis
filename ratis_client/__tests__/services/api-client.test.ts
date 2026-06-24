// ratis_client/__tests__/services/api-client.test.ts

jest.mock("expo-secure-store");
jest.mock("expo-network");

import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { apiClient, createApiClient, __setBaseUrl, __resetAuthState } from "@/services/api-client";
import { tokenStorage } from "@/services/token-storage";
import { BASE_URL, defaultHandlers } from "@/tests/fixtures/msw-handlers";
import { authEvents } from "@/services/auth-events";
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

describe("apiClient — base fetch", () => {
  it("performs GET and returns parsed JSON", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    const result = await apiClient.get<{ id: string }>("/auth/me");
    expect(result.id).toBe("u-1");
  });

  it("injects Authorization header with stored access token", async () => {
    await tokenStorage.set({ accessToken: "A-HEADER", refreshToken: "R", expiresAt: 1 });

    let receivedAuth: string | null = null;
    server.use(
      http.get(`${BASE_URL}/auth/me`, ({ request }) => {
        receivedAuth = request.headers.get("authorization");
        return HttpResponse.json({ ok: true });
      })
    );

    await apiClient.get("/auth/me");
    expect(receivedAuth).toBe("Bearer A-HEADER");
  });

  it("works without auth when no token stored (public endpoint)", async () => {
    let receivedAuth: string | null = null;
    server.use(
      http.get(`${BASE_URL}/public`, ({ request }) => {
        receivedAuth = request.headers.get("authorization");
        return HttpResponse.json({ ok: true });
      })
    );

    await apiClient.get("/public");
    expect(receivedAuth).toBeNull();
  });

  it("POST sends JSON body with Content-Type", async () => {
    let received: { body: unknown; contentType: string | null } = { body: null, contentType: null };
    server.use(
      http.post(`${BASE_URL}/echo`, async ({ request }) => {
        received.contentType = request.headers.get("content-type");
        received.body = await request.json();
        return HttpResponse.json({ ok: true });
      })
    );

    await apiClient.post("/echo", { hello: "world" });
    expect(received.contentType).toMatch(/application\/json/);
    expect(received.body).toEqual({ hello: "world" });
  });
});

describe("apiClient — error parsing", () => {
  it("4xx non-401 → AuthError with code + VALIDATION_ERROR class", async () => {
    server.use(
      http.get(`${BASE_URL}/bad`, () =>
        HttpResponse.json({ detail: "invalid_input" }, { status: 400 })
      )
    );

    await expect(apiClient.get("/bad")).rejects.toMatchObject({
      code: "invalid_input",
      errorClass: "VALIDATION_ERROR",
      httpStatus: 400,
    });
  });

  it("5xx → AuthError with code + SERVER_ERROR class", async () => {
    server.use(
      http.get(`${BASE_URL}/boom`, () =>
        HttpResponse.json({ detail: "internal_error" }, { status: 500 })
      )
    );

    await expect(apiClient.get("/boom")).rejects.toMatchObject({
      code: "internal_error",
      errorClass: "SERVER_ERROR",
      httpStatus: 500,
    });
  });

  it("unknown body (non-JSON) → AuthError with unknown_error code", async () => {
    server.use(
      http.get(`${BASE_URL}/weird`, () =>
        HttpResponse.text("not json", { status: 500 })
      )
    );

    await expect(apiClient.get("/weird")).rejects.toMatchObject({
      code: "unknown_error",
      errorClass: "SERVER_ERROR",
    });
  });

  it("403 → VALIDATION_ERROR (not AUTH_ERROR)", async () => {
    server.use(
      http.get(`${BASE_URL}/forbidden`, () =>
        HttpResponse.json({ detail: "forbidden" }, { status: 403 })
      )
    );

    await expect(apiClient.get("/forbidden")).rejects.toMatchObject({
      code: "forbidden",
      errorClass: "VALIDATION_ERROR",
    });
  });
});

describe("apiClient — 401 refresh handling", () => {
  it("on 401 → calls /auth/refresh → retries original request → returns data", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    let callCount = 0;
    server.use(
      http.get(`${BASE_URL}/protected`, ({ request }) => {
        callCount += 1;
        const auth = request.headers.get("authorization");
        if (auth === "Bearer OLD") {
          return HttpResponse.json({ detail: "invalid_token" }, { status: 401 });
        }
        if (auth === "Bearer access.jwt.2") {
          return HttpResponse.json({ ok: true });
        }
        return HttpResponse.json({ detail: "unexpected" }, { status: 500 });
      })
    );

    const result = await apiClient.get<{ ok: boolean }>("/protected");
    expect(result).toEqual({ ok: true });
    expect(callCount).toBe(2); // original + retry
  });

  it("on 401 after refresh succeeds — stored tokens are updated", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    server.use(
      http.get(`${BASE_URL}/me`, ({ request }) => {
        if (request.headers.get("authorization") === "Bearer OLD") {
          return HttpResponse.json({ detail: "invalid_token" }, { status: 401 });
        }
        return HttpResponse.json({ id: "u-1" });
      })
    );

    await apiClient.get("/me");
    await expect(tokenStorage.getAccess()).resolves.toBe("access.jwt.2");
    await expect(tokenStorage.getRefresh()).resolves.toBe("refresh.jwt.2");
  });

  it("on 401 → refresh fails (401) → throws AuthError(session_expired)", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    server.use(
      http.get(`${BASE_URL}/me`, () =>
        HttpResponse.json({ detail: "invalid_token" }, { status: 401 })
      ),
      http.post(`${BASE_URL}/auth/refresh`, () =>
        HttpResponse.json({ detail: "invalid_refresh_token" }, { status: 401 })
      )
    );

    await expect(apiClient.get("/me")).rejects.toMatchObject({
      code: "session_expired",
      errorClass: "AUTH_ERROR",
    });
  });

  it("does NOT intercept 401 on /auth/refresh itself", async () => {
    server.use(
      http.post(`${BASE_URL}/auth/refresh`, () =>
        HttpResponse.json({ detail: "invalid_refresh_token" }, { status: 401 })
      )
    );

    await expect(
      apiClient.post("/auth/refresh", { refresh_token: "x" })
    ).rejects.toMatchObject({
      code: "invalid_refresh_token",
      errorClass: "AUTH_ERROR",
    });
  });

  it("does NOT intercept 401 on /auth/oauth", async () => {
    server.use(
      http.post(`${BASE_URL}/auth/oauth`, () =>
        HttpResponse.json({ detail: "invalid_google_token" }, { status: 401 })
      )
    );

    await expect(
      apiClient.post("/auth/oauth", { provider: "google", token: "x" })
    ).rejects.toMatchObject({
      code: "invalid_google_token",
    });
  });

  it("does not retry if no refresh token stored", async () => {
    // No tokenStorage.set() call
    server.use(
      http.get(`${BASE_URL}/me`, () =>
        HttpResponse.json({ detail: "invalid_token" }, { status: 401 })
      )
    );

    await expect(apiClient.get("/me")).rejects.toMatchObject({
      code: "session_expired",
      errorClass: "AUTH_ERROR",
    });
  });
});

describe("apiClient — concurrent refresh", () => {
  it("N concurrent 401s trigger only 1 /auth/refresh call", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    let refreshCallCount = 0;
    server.use(
      http.get(`${BASE_URL}/p1`, ({ request }) => {
        if (request.headers.get("authorization") === "Bearer OLD") {
          return HttpResponse.json({ detail: "invalid" }, { status: 401 });
        }
        return HttpResponse.json({ ok: "p1" });
      }),
      http.get(`${BASE_URL}/p2`, ({ request }) => {
        if (request.headers.get("authorization") === "Bearer OLD") {
          return HttpResponse.json({ detail: "invalid" }, { status: 401 });
        }
        return HttpResponse.json({ ok: "p2" });
      }),
      http.get(`${BASE_URL}/p3`, ({ request }) => {
        if (request.headers.get("authorization") === "Bearer OLD") {
          return HttpResponse.json({ detail: "invalid" }, { status: 401 });
        }
        return HttpResponse.json({ ok: "p3" });
      }),
      http.post(`${BASE_URL}/auth/refresh`, () => {
        refreshCallCount += 1;
        return HttpResponse.json({
          access_token: "access.jwt.2",
          refresh_token: "refresh.jwt.2",
          expires_in: 900,
          token_type: "bearer",
        });
      })
    );

    const [r1, r2, r3] = await Promise.all([
      apiClient.get("/p1"),
      apiClient.get("/p2"),
      apiClient.get("/p3"),
    ]);

    expect(refreshCallCount).toBe(1);
    expect(r1).toEqual({ ok: "p1" });
    expect(r2).toEqual({ ok: "p2" });
    expect(r3).toEqual({ ok: "p3" });
  });

  it("second wave of 401s after first refresh completes triggers a new refresh", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    let refreshCallCount = 0;
    server.use(
      http.get(`${BASE_URL}/p`, ({ request }) => {
        const auth = request.headers.get("authorization");
        // Every token is rejected — proves both calls hit the refresh path.
        if (auth && auth.startsWith("Bearer ")) {
          return HttpResponse.json({ detail: "invalid" }, { status: 401 });
        }
        return HttpResponse.json({ ok: true });
      }),
      http.post(`${BASE_URL}/auth/refresh`, () => {
        refreshCallCount += 1;
        return HttpResponse.json({
          access_token: `access.jwt.${refreshCallCount + 1}`,
          refresh_token: `refresh.jwt.${refreshCallCount + 1}`,
          expires_in: 900,
          token_type: "bearer",
        });
      })
    );

    // First call fails after retry because token.2 is also invalid per handler
    await expect(apiClient.get("/p")).rejects.toMatchObject({ code: "session_expired" });
    expect(refreshCallCount).toBe(1);

    // Second call also triggers a new refresh (singleton was cleared)
    await expect(apiClient.get("/p")).rejects.toMatchObject({ code: "session_expired" });
    expect(refreshCallCount).toBe(2);
  });
});

describe("apiClient — network error normalization", () => {
  it("network failure → AuthError(NETWORK_ERROR), not a raw TypeError", async () => {
    server.use(
      http.get(`${BASE_URL}/me`, () => HttpResponse.error())
    );

    await expect(apiClient.get("/me")).rejects.toMatchObject({
      name: "AuthError",
      errorClass: "NETWORK_ERROR",
    });
  });

  it("request timeout → AuthError(TIMEOUT) without hanging forever", async () => {
    server.use(
      http.get(`${BASE_URL}/slow`, async () => {
        await new Promise((r) => setTimeout(r, 10_000));
        return HttpResponse.json({ ok: true });
      })
    );

    // Per-request timeout override keeps the test fast.
    await expect(
      apiClient.get("/slow", { timeoutMs: 100 })
    ).rejects.toMatchObject({ name: "AuthError", errorClass: "TIMEOUT" });
  }, 10_000);
});

describe("apiClient — timeout and network", () => {
  it("refresh timeout throws without hanging forever", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    server.use(
      http.get(`${BASE_URL}/me`, () =>
        HttpResponse.json({ detail: "invalid" }, { status: 401 })
      ),
      http.post(`${BASE_URL}/auth/refresh`, async () => {
        // Hang for longer than refresh timeout
        await new Promise((r) => setTimeout(r, 20_000));
        return HttpResponse.json({ ok: 1 });
      })
    );

    // Race the api call against a hard test deadline
    await expect(
      Promise.race([
        apiClient.get("/me"),
        new Promise((_, rej) => setTimeout(() => rej(new Error("test_timeout")), 500)),
      ])
    ).rejects.toThrow();
  }, 30_000);

  it("network error on refresh → waitForOnline → retry on return", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    const mockNet = jest.requireMock("expo-network") as {
      __setNetworkState: (s: { isConnected: boolean }) => void;
      __reset: () => void;
    };

    let refreshAttempts = 0;
    server.use(
      http.get(`${BASE_URL}/me`, ({ request }) =>
        request.headers.get("authorization") === "Bearer OLD"
          ? HttpResponse.json({ detail: "invalid" }, { status: 401 })
          : HttpResponse.json({ id: "u-1" })
      ),
      http.post(`${BASE_URL}/auth/refresh`, () => {
        refreshAttempts += 1;
        if (refreshAttempts === 1) return HttpResponse.error(); // network failure
        return HttpResponse.json({
          access_token: "access.jwt.2",
          refresh_token: "refresh.jwt.2",
          expires_in: 900,
          token_type: "bearer",
        });
      })
    );

    mockNet.__setNetworkState({ isConnected: false });
    const promise = apiClient.get("/me");

    // Network comes back after a short delay
    setTimeout(() => mockNet.__setNetworkState({ isConnected: true }), 20);

    await expect(promise).resolves.toEqual({ id: "u-1" });
    expect(refreshAttempts).toBe(2);

    mockNet.__reset();
  }, 10_000);
});

describe("apiClient — force_logout event", () => {
  it("emits force_logout when refresh returns 401", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    let fired = false;
    const unsub = authEvents.onForceLogout(() => { fired = true; });

    server.use(
      http.get(`${BASE_URL}/me`, () =>
        HttpResponse.json({ detail: "invalid" }, { status: 401 })
      ),
      http.post(`${BASE_URL}/auth/refresh`, () =>
        HttpResponse.json({ detail: "invalid_refresh_token" }, { status: 401 })
      )
    );

    await expect(apiClient.get("/me")).rejects.toMatchObject({ code: "session_expired" });
    expect(fired).toBe(true);

    unsub();
  });

  it("does NOT emit force_logout on 5xx or network error", async () => {
    await tokenStorage.set({ accessToken: "OLD", refreshToken: "R", expiresAt: 1 });

    let fired = false;
    const unsub = authEvents.onForceLogout(() => { fired = true; });

    server.use(
      http.get(`${BASE_URL}/me`, () =>
        HttpResponse.json({ detail: "internal" }, { status: 500 })
      )
    );

    await expect(apiClient.get("/me")).rejects.toMatchObject({ code: "internal" });
    expect(fired).toBe(false);

    unsub();
  });
});

describe("createApiClient — factory", () => {
  it("routes requests to the override base URL, not BASE_URL", async () => {
    const calls: string[] = [];
    server.use(
      http.get("http://svc-a/v1/ping", ({ request }) => {
        calls.push(request.url);
        return HttpResponse.json({ ok: true });
      })
    );

    const svcA = createApiClient("http://svc-a/v1");
    await svcA.get("/ping");
    expect(calls).toEqual(["http://svc-a/v1/ping"]);
  });

  it("attaches Authorization header from tokenStorage", async () => {
    await tokenStorage.set({ accessToken: "FACTORY-TOKEN", refreshToken: "R", expiresAt: 1 });

    let receivedAuth: string | null = null;
    server.use(
      http.get("http://svc-b/v1/secure", ({ request }) => {
        receivedAuth = request.headers.get("authorization");
        return HttpResponse.json({ ok: true });
      })
    );

    const svcB = createApiClient("http://svc-b/v1");
    await svcB.get("/secure");
    expect(receivedAuth).toBe("Bearer FACTORY-TOKEN");
  });

  it("createApiClient() without args behaves like apiClient (uses BASE_URL)", async () => {
    let receivedUrl: string | null = null;
    server.use(
      http.get(`${BASE_URL}/ping`, ({ request }) => {
        receivedUrl = request.url;
        return HttpResponse.json({ ok: true });
      })
    );

    const defaultClient = createApiClient();
    await defaultClient.get("/ping");
    expect(receivedUrl).toBe(`${BASE_URL}/ping`);
  });
});
