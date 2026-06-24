// ratis_client/services/api-client.ts

import { tokenStorage } from "@/services/token-storage";
import { AuthError, ErrorClass } from "@/types/auth";
import { waitForOnline } from "@/services/wait-for-online";
import { authEvents } from "@/services/auth-events";
import { requireEnv } from "@/services/env";
import { TIMEOUTS } from "@/constants/Timeouts";

let BASE_URL = "";

export function __setBaseUrl(url: string): void {
  BASE_URL = url;
}

function setBaseUrlFromEnv(): void {
  if (BASE_URL) return;
  // requireEnv throws loudly if the var is missing or empty — no silent
  // fallback. A misconfigured bundle is a config bug, not a runtime concern.
  // Resolution is lazy (called from `request()`) so the throw only happens
  // when an actual HTTP call would have been made — login fails clearly,
  // ErrorBoundary catches, Sentry logs, we know exactly what to fix.
  BASE_URL = requireEnv("EXPO_PUBLIC_API_URL", process.env.EXPO_PUBLIC_API_URL);
}

const NO_INTERCEPT_PATHS = ["/auth/refresh", "/auth/oauth"];

function shouldIntercept401(path: string): boolean {
  return !NO_INTERCEPT_PATHS.some((p) => path.startsWith(p));
}

function classifyError(status: number): ErrorClass {
  if (status === 401) return "AUTH_ERROR";
  if (status >= 500) return "SERVER_ERROR";
  if (status >= 400) return "VALIDATION_ERROR";
  return "SERVER_ERROR";
}

async function parseError(resp: Response): Promise<AuthError> {
  let detail = "unknown_error";
  try {
    const body = await resp.clone().json();
    if (body && typeof body.detail === "string") detail = body.detail;
  } catch {
    // non-JSON body
  }
  return new AuthError(detail, classifyError(resp.status), resp.status);
}

let refreshPromise: Promise<string | null> | null = null;

// Test-only: clear the in-flight refresh singleton so pending promises
// from a prior test cannot leak into the next one.
export function __resetAuthState(): void {
  refreshPromise = null;
}

async function refreshOnce(): Promise<string | null> {
  const refreshToken = await tokenStorage.getRefresh();
  if (!refreshToken) return null;

  const attempt = async (): Promise<Response | null> => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUTS.REFRESH_TOKEN);
    try {
      return await fetch(BASE_URL + "/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        signal: ctrl.signal,
      });
    } catch {
      return null; // network / abort
    } finally {
      clearTimeout(timer);
    }
  };

  let resp = await attempt();
  if (!resp) {
    // Network failure — wait for connectivity then retry ONCE
    const online = await waitForOnline(TIMEOUTS.WAIT_FOR_ONLINE);
    if (!online) throw new AuthError("offline", "NETWORK_ERROR");
    resp = await attempt();
    if (!resp) throw new AuthError("server_timeout", "TIMEOUT");
  }

  if (!resp.ok) return null;

  const data = (await resp.json()) as {
    access_token: string;
    refresh_token: string;
    expires_in: number;
  };

  await tokenStorage.set({
    accessToken: data.access_token,
    refreshToken: data.refresh_token,
    expiresAt: Date.now() + data.expires_in * 1000,
  });

  return data.access_token;
}

function doRefresh(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = refreshOnce().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

type Init = RequestInit & {
  skipAuth?: boolean;
  /** Per-request timeout override (ms). Defaults to TIMEOUTS.DEFAULT_REQUEST. */
  timeoutMs?: number;
};

async function rawFetch(path: string, init: Init, base: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (!init.skipAuth) {
    const access = await tokenStorage.getAccess();
    if (access) headers.set("Authorization", `Bearer ${access}`);
  }
  // Wrap fetch in an AbortController + timeout, and normalize the rejection.
  // Without this, a network outage surfaces as a raw, untyped `TypeError`
  // ("Network request failed") that every caller would have to special-case.
  // Callers expect AuthError — convert here so the failure is classified
  // (NETWORK_ERROR vs TIMEOUT) at a single chokepoint.
  const { skipAuth: _skipAuth, timeoutMs, ...rest } = init;
  const ctrl = new AbortController();
  const timer = setTimeout(
    () => ctrl.abort(),
    timeoutMs ?? TIMEOUTS.DEFAULT_REQUEST,
  );
  try {
    return await fetch(base + path, { ...rest, headers, signal: ctrl.signal });
  } catch {
    // An aborted fetch means our timeout fired; anything else is a transport
    // failure (no connectivity, DNS, TLS, etc.).
    if (ctrl.signal.aborted) {
      throw new AuthError("server_timeout", "TIMEOUT");
    }
    throw new AuthError("offline", "NETWORK_ERROR");
  } finally {
    clearTimeout(timer);
  }
}

async function request<T>(
  path: string,
  init: Init = {},
  overrideBase?: string,
): Promise<T> {
  if (!overrideBase) setBaseUrlFromEnv();
  const base = overrideBase ?? BASE_URL;

  let resp = await rawFetch(path, init, base);

  if (resp.status === 401 && shouldIntercept401(path)) {
    const newAccess = await doRefresh();
    if (!newAccess) {
      authEvents.emitForceLogout();
      throw new AuthError("session_expired", "AUTH_ERROR", 401);
    }
    // Retry ONCE with the new token
    resp = await rawFetch(path, init, base);
    if (resp.status === 401) {
      authEvents.emitForceLogout();
      throw new AuthError("session_expired", "AUTH_ERROR", 401);
    }
  }

  if (!resp.ok) throw await parseError(resp);
  return (await resp.json()) as T;
}

// Factory permettant de créer un client avec une base URL spécifique.
// Utilisé pour les services secondaires (rewards, product_analyser).
// Sans argument, se comporte comme l'apiClient principal (BASE_URL).
//
// `baseUrl` peut être une string (résolue eagerly à l'appel de createApiClient)
// OU un thunk `() => string` (résolu lazy à chaque requête). Le thunk permet
// d'utiliser `requireEnv` qui throw — sans la lazy-resolution un env var
// manquant casserait l'import chain au module load.
export function createApiClient(baseUrl?: string | (() => string)) {
  const resolve = (): string | undefined =>
    typeof baseUrl === "function" ? baseUrl() : baseUrl;
  return {
    get<T>(path: string, init?: Init) {
      return request<T>(path, { ...init, method: "GET" }, resolve());
    },
    post<T>(path: string, body?: unknown, init?: Init) {
      return request<T>(
        path,
        {
          ...init,
          method: "POST",
          body: body !== undefined ? JSON.stringify(body) : undefined,
        },
        resolve(),
      );
    },
    patch<T>(path: string, body?: unknown, init?: Init) {
      return request<T>(
        path,
        {
          ...init,
          method: "PATCH",
          body: body !== undefined ? JSON.stringify(body) : undefined,
        },
        resolve(),
      );
    },
    delete<T>(path: string, init?: Init) {
      return request<T>(path, { ...init, method: "DELETE" }, resolve());
    },
  };
}

export const apiClient = createApiClient();
