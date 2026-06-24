---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_AUTH]
status: in-progress
tags: [auth, oauth, client, expo, jwt]
updated: 2026-04-24
---

# ratis_client — ARCH authentication

> Mobile auth stack: `expo-secure-store` (tokens), `expo-auth-session` + `expo-apple-authentication`, `api-client.ts` (Bearer inject + 401 refresh singleton), `AuthContext`. V1 complete, Sentry wiring + OAuth client IDs pending.
> @tags: auth oauth client expo jwt secure-store apple-auth google refresh-singleton authcontext bearer login
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_CLIENT]] · Relations: [[ARCH_AUTH]]

> Status: in progress — V1 implementation complete, Sentry wiring + OAuth client IDs pending
> Branch: `feature/auth-oauth`

---

## Implementation Checklist

**Frontend checklist:**
- [x] Dependencies installed (`expo-secure-store`, `expo-apple-authentication`, `expo-auth-session`, `expo-web-browser`, `expo-crypto`, `expo-localization`, `expo-network`)
- [x] `services/token-storage.ts` — SecureStore wrapper (get/set/clear)
- [x] `services/api-client.ts` — fetch wrapper with Bearer injection + 401 handler + refresh singleton
- [x] `services/auth-service.ts` — `signInWithApple()`, `signInWithGoogle()`, `signOut()`
- [x] `contexts/AuthContext.tsx` — global state `{user, status, error}`
- [x] `hooks/useAuth.ts` — consumer hook
- [x] `hooks/useSlowRequestIndicator.ts` — progressive UI feedback
- [x] `components/NetworkStatusBanner.tsx` — decorative offline banner
- [x] `app/(auth)/login.tsx` — login screen (2 OAuth buttons + legal notices)
- [x] `app/_layout.tsx` — router guard (redirect to login if unauthenticated)
- [x] Tests written in TDD (before the code)
- [x] Tests: auth-service (mock provider SDK + backend)
- [x] Tests: api-client (401 refresh, retry, Promise singleton serialization)
- [x] Tests: AuthContext (state transitions)
- [x] Tests: waitForOnline event-driven
- [ ] Env variables: `GOOGLE_CLIENT_ID_IOS`, `GOOGLE_CLIENT_ID_ANDROID`, `APPLE_SERVICES_ID`
- [x] `app.json`: scheme `ratis`, bundleIdentifier, usesAppleSignIn
- [x] i18n documentation for errors (`auth.oauth_cancelled`, `auth.offline`, etc.)
- [x] Frontend CI pipeline green (to be created if absent)

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Context](#context)
- [Global architecture](#global-architecture)
- [Detailed login flow](#detailed-login-flow)
- [Refresh token + 401 handler](#refresh-token--401-handler)
- [Global state & AuthContext](#global-state--authcontext)
- [Error handling](#error-handling)
- [Router guards & navigation](#router-guards--navigation)
- [Tests](#tests)
- [Parameters](#parameters)
- [Rules](#rules)
- [Out of scope](#out-of-scope)

---

## Context

Read before starting:
- `CLAUDE.md` — stack, conventions, i18n rules
- `DECISIONS_ACTED.md` — DA-26 (Expo managed)
- `PRODUCT.md` — vision, red lines
- `ratis_client/ARCH_expo_strategy.md` — Expo migration signals

**Pre-brainstorming decisions (validated in this session):**
- Auth V1 = **OAuth-only** (Google + Apple), no email/password in the frontend
- Email/password endpoints exist on the backend — preserved, inactive on the app side, to be reactivated in V2 when email infrastructure is ready
- No email infrastructure → no forgot-password (OAuth handles reset on the provider side)
- Refresh strategy: **reactive on 401**, not proactive
- State stack: Context + useReducer, no external library (Zustand/Redux disqualified for this scope)

**Backend dependencies (already in place):**
- `POST /api/v1/auth/oauth` — exchanges idToken Google/Apple → Ratis JWT pair
- `POST /api/v1/auth/refresh` — refresh token rotation with revocation
- `GET  /api/v1/auth/me` — current user profile
- `POST /api/v1/account/logout` — revokes the refresh token server-side

---

## Global architecture

```
┌────────────────────────────────────────────────────┐
│  APP (Expo / React Native)                         │
│                                                    │
│  app/(auth)/login.tsx                              │
│   └─► services/auth-service.ts                     │
│         └─► SDK provider (expo-apple-auth /        │
│             expo-auth-session Google)              │
│         └─► services/api-client.ts                 │
│               └─► services/token-storage.ts        │
│                     └─► expo-secure-store          │
│                         (Keychain iOS /            │
│                          Keystore Android)         │
│         └─► contexts/AuthContext.tsx               │
│               └─► hooks/useAuth.ts (consumer)      │
└────────────────────────────────────────────────────┘
                          │ HTTPS
                          ▼
┌────────────────────────────────────────────────────┐
│  BACKEND (ratis_auth)                              │
└────────────────────────────────────────────────────┘
```

**Separation of concerns:**

| Module | Single responsibility |
|---|---|
| `token-storage` | Persist tokens securely (SecureStore only) |
| `api-client` | HTTP transport, auth injection, automatic refresh on 401 |
| `auth-service` | Orchestrate the OAuth flow (SDK → backend → storage → me) |
| `AuthContext` | Source of truth for the `{user, status}` state for the UI |
| `useAuth` | Read access + actions (signIn/signOut) for components |
| `login.tsx` | Pure UI — 2 buttons + legal notices + UI indicators |

---

## Detailed login flow

### Apple Sign-In

Module: `expo-apple-authentication` (native iOS, not available elsewhere).

```ts
const isAvailable = await AppleAuth.isAvailableAsync();
if (!isAvailable) {
  // Android / iOS < 13 / web: button hidden
  return;
}

const rawNonce = Crypto.randomUUID();
const hashedNonce = await Crypto.digestStringAsync(
  Crypto.CryptoDigestAlgorithm.SHA256,
  rawNonce,
);

const credential = await AppleAuth.signInAsync({
  requestedScopes: [
    AppleAuth.AppleAuthenticationScope.EMAIL,
    AppleAuth.AppleAuthenticationScope.FULL_NAME,
  ],
  nonce: hashedNonce,
});

// credential.identityToken = Apple-signed JWT → sent to backend
await exchangeOAuthToken("apple", credential.identityToken);
```

**Apple edge cases:**

| Case | Behavior |
|---|---|
| iOS < 13 or Android | Apple button hidden |
| User cancels | `ERR_CANCELED` → return to login screen, no error |
| iCloud unavailable | `ERR_REQUEST_FAILED` → toast "apple_unavailable" |
| Apple token revoked | 401 backend → toast "auth_failed" |

### Google Sign-In

Module: `expo-auth-session` + `expo-web-browser` (web OAuth flow, compatible with Expo Go, no dev client required in V1).

```ts
WebBrowser.maybeCompleteAuthSession();

const rawNonce = Crypto.randomUUID();
const hashedNonce = await Crypto.digestStringAsync(
  Crypto.CryptoDigestAlgorithm.SHA256,
  rawNonce,
);

const discovery = {
  authorizationEndpoint: "https://accounts.google.com/o/oauth2/v2/auth",
  tokenEndpoint: "https://oauth2.googleapis.com/token",
};

const request = new AuthSession.AuthRequest({
  clientId: Constants.expoConfig.extra.googleClientId,
  scopes: ["openid", "email", "profile"],
  redirectUri: AuthSession.makeRedirectUri({ scheme: "ratis" }),
  responseType: "id_token",
  extraParams: { nonce: hashedNonce },
});

const result = await request.promptAsync(discovery);
if (result.type !== "success") return; // cancel / error

await exchangeOAuthToken("google", result.params.id_token);
```

**Choice: `response_type=id_token` (implicit flow).** We only need a signed JWT to verify on our side — no need to call Google APIs with an access token. Simpler, no client secret to embed in the app.

**Security:** SHA-256 nonce hash → Google signs it into the idToken → prevents replay attacks.

**Google edge cases:**

| Case | Behavior |
|---|---|
| User closes the browser | `result.type === "cancel"` → return to login |
| No network | `result.type === "error"` → toast "network_error" |
| Client ID misconfigured | 401 backend `invalid_google_token` → toast "configuration_error" + log |

### Operation order — `auth-service.signIn()`

```
1. Obtain idToken from provider (native SDK or web session)
2. POST /auth/oauth { provider, token: idToken, timezone } → {access, refresh, expires_in}
3. Store tokens in SecureStore
4. GET /auth/me → user profile
5. Update AuthContext.user
```

**Atomicity rule:** no global state is mutated before step 5. If any step fails, the error is propagated and we remain on `status: 'unauthenticated'`.

**In case of failure between steps 3 and 4:** tokens are persisted but `/auth/me` failed. The next app startup will attempt `/auth/me` again from the persisted tokens — resilient behavior, no cleanup required.

---

## Refresh token + 401 handler

### Lifecycle

```
Access token  : 15 minutes (backend)
Refresh token : 30 days, rotation on each use (backend revokes the old one, issues a new one)
```

### Strategy: reactive

No proactive JS timer. We attempt the request → if 401, we refresh then retry **exactly once**.

Rationale:
- Simplicity (no timer to maintain, no race conditions with background/foreground)
- No dependency on the client clock (which can drift)
- More battery-efficient

### Behavior on 401

```ts
async function request(path, init) {
  const access = await tokenStorage.getAccess();
  let resp = await fetch(url + path, withAuth(init, access));

  if (resp.status !== 401) return handle(resp);
  if (isRefreshOrOauthPath(path)) return handle(resp); // no intercept on these endpoints

  const newAccess = await refreshOrLogout();
  if (!newAccess) throw new AuthError("session_expired");

  resp = await fetch(url + path, withAuth(init, newAccess));
  return handle(resp);
}
```

### Refresh serialization (Promise singleton)

If N parallel requests all receive a 401, we do NOT make N concurrent refreshes (the backend would revoke N-1 tokens during rotation, breaking the session).

```ts
let refreshPromise: Promise<string | null> | null = null;

async function refreshOrLogout(): Promise<string | null> {
  if (refreshPromise) return refreshPromise; // reuse existing

  refreshPromise = (async () => {
    try {
      // ... refresh logic below
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}
```

### Degraded network handling (event-driven)

Problem: in stores, 3G/4G connections in transition, underground parking → exit, etc. A blocking pre-check `NetInfo.isConnected` risks false positives (NetInfo lies on ~1-3% of cases, up to 10% in transition).

Chosen pattern: **always attempt fetch first**. If fetch fails due to network issues (fast native detection < 500ms), we use an **event-driven listener** that waits for the network to return (max 15s) before retrying exactly once.

```ts
async function waitForOnline(maxWaitMs = 15_000): Promise<boolean> {
  const current = await Network.getNetworkStateAsync();
  if (current.isConnected) return true;

  return new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => { unsub(); resolve(false); }, maxWaitMs);
    const unsub = Network.addNetworkStateListener(state => {
      if (state.isConnected) {
        clearTimeout(timer);
        unsub();
        resolve(true);
      }
    });
  });
}
```

**Network return detection latency: ~0 ms** (event-driven vs 0-3s with polling). No CPU consumed during the wait.

### Full `refreshOrLogout` flow

```
1. Fetch refresh token from SecureStore
   ├─ absent → return null (→ logout)
2. POST /auth/refresh {refresh_token} with AbortController (15s timeout)
   ├─ Success → store new tokens, return access
   ├─ 401/403 → refresh revoked → forceLogout(), return null
   ├─ Network error → waitForOnline(15s)
   │    ├─ Network returns → retry ONCE
   │    └─ Timeout → throw AuthError("offline") (no logout)
   ├─ Timeout (15s server) → throw AuthError("server_timeout") (no logout)
   └─ 5xx → throw AuthError("service_unavailable") (no logout)
```

### Critical rules

1. **One retry maximum.** If the retry after refresh still returns 401, the session is considered dead.
2. **Failed refresh = forced logout.** `401/403` on `/auth/refresh` → clear SecureStore, reset AuthContext, navigate to `/login`.
3. **Do not intercept `/auth/refresh` or `/auth/oauth` with the 401 handler.** Otherwise infinite loop.
4. **Timeout ≠ logout.** A timeout or 5xx on refresh bubbles the error to the UI without destroying the session.
5. **Theft detection (refresh reuse)**: handled backend-side — if a revoked refresh is reused, the backend invalidates all the user's refresh tokens. On the frontend we just receive a 401 and logout — zero dedicated code.

### Progressive UI feedback (Option C)

Hook `useSlowRequestIndicator` that emits timing events during the wait:

```
T+0s  : simple spinner
T+3s  : message "Connexion en cours..."
T+8s  : message "La connexion est lente, patientez..."
T+12s : message "Prend plus de temps que d'habitude"
T+15s : error displayed or retry succeeded
```

Reusable for all long requests (receipt uploads, list optimization).

---

## Global state & AuthContext

### 4-state state machine

```
initializing → authenticated      (boot OK)
initializing → unauthenticated    (no tokens or /auth/me fails)
unauthenticated → authenticating  (signIn triggered)
authenticating → authenticated    (OAuth success)
authenticating → unauthenticated  (OAuth failure)
authenticated → unauthenticated   (signOut or force_logout)
```

### Context shape

```ts
type AuthState =
  | { status: "initializing" }
  | { status: "unauthenticated"; error: AuthError | null }
  | { status: "authenticating"; provider: "apple" | "google" }
  | { status: "authenticated"; user: User };

type AuthContextValue = AuthState & {
  signIn: (provider: "apple" | "google") => Promise<void>;
  signOut: () => Promise<void>;
};
```

Discriminated union → TypeScript forces consumers to handle each status. Impossible to access `user` when `status !== "authenticated"`.

### Reducer + 7 actions

```ts
type Action =
  | { type: "BOOT_SUCCESS"; user: User }
  | { type: "BOOT_FAIL" }
  | { type: "SIGNIN_START"; provider: "apple" | "google" }
  | { type: "SIGNIN_SUCCESS"; user: User }
  | { type: "SIGNIN_FAIL"; error: AuthError }
  | { type: "SIGNOUT" }
  | { type: "FORCE_LOGOUT" };
```

Each action produces a complete state (no partial mutation). Reducer testable without rendering — `expect(authReducer(state, action)).toEqual(...)`.

### App boot

```
useEffect on AuthProvider mount:
  1. Read SecureStore → tokens
  2. If absent → BOOT_FAIL (status: unauthenticated)
  3. Otherwise → GET /auth/me (with automatic refresh on 401)
     ├─ Success → BOOT_SUCCESS(user)
     └─ Failure → BOOT_FAIL
```

### api-client → AuthContext communication

`api-client` is a pure JS module, not React. To force a logout (refresh revoked, user deleted), it emits an event via EventEmitter:

```ts
// services/auth-events.ts
export const authEvents = new EventEmitter();

// api-client.ts: after definitive refresh 401
authEvents.emit("force_logout");

// AuthProvider: subscribe
authEvents.on("force_logout", () => {
  tokenStorage.clear();
  dispatch({ type: "FORCE_LOGOUT" });
});
```

**EventEmitter rationale:** avoids the import cycle (api-client ← auth-service ← AuthContext). Decoupled unidirectional channel, easy to mock in tests.

### useReducer rather than useState

- Discrete transitions (7 actions) → reducer = readable spec
- `useState` would expose raw `setState` → invites producing states that are "typed OK but semantically broken"
- Reducer testable in isolation without rendering

### Position in the tree

```tsx
// app/_layout.tsx
<AuthProvider>
  <NetworkBannerProvider>
    <ThemeProvider>
      <Slot />
    </ThemeProvider>
  </NetworkBannerProvider>
</AuthProvider>
```

At the highest level so route guards can read the state from the first render.

### Consumer hook

```ts
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
```

### Performance

The Context re-renders its subtree on every value change. The auth state changes ~5-10× per session (login, force logout, user reload). Not a perf concern. If optimization is ever needed, split into `AuthStateContext` + `AuthActionsContext` — not V1.

---

## Error handling

### Taxonomy — 6 families

| Family | Origin | UI behavior |
|---|---|---|
| `NETWORK_ERROR` | fetch fails (offline, DNS, TLS) | Toast "No connection", offline banner, no logout |
| `TIMEOUT` | AbortController | Toast "Service unavailable, please retry", no logout |
| `AUTH_ERROR` | Explicit 401 from backend (refresh revoked, user deleted) | Force logout → login screen |
| `VALIDATION_ERROR` | non-401 4xx with backend `detail` | Inline i18n message, no logout |
| `SERVER_ERROR` | 5xx | Toast "An error occurred", retry possible, no logout |
| `CANCELLED` | User cancels OAuth prompt | Silent, return to login |

### Error type

```ts
export class AuthError extends Error {
  constructor(
    public readonly code: string,         // i18n key ("invalid_credentials")
    public readonly errorClass: ErrorClass,
    public readonly httpStatus?: number,
    public readonly details?: unknown,
  ) {
    super(code);
    this.name = "AuthError";
  }
}
```

The `code` is the **root i18n key** → 1-1 mapping with backend codes:

```
backend "detail"         → i18n key                          → FR message
"invalid_credentials"    → "auth.invalid_credentials"        → "Identifiants invalides"
"invalid_google_token"   → "auth.google_unavailable"         → "Connexion Google impossible"
"invalid_apple_token"    → "auth.apple_unavailable"          → "Connexion Apple impossible"
"invalid_refresh_token"  → (force logout, no message)        → —
"upstream_service_error" → "errors.provider_unavailable"     → "Service indisponible"
"offline"                → "errors.offline"                  → "Pas de connexion"
"server_timeout"         → "errors.server_timeout"           → "Le service met du temps à répondre"
```

### HTTP → AuthError parser

```ts
async function handle(resp: Response): Promise<Response> {
  if (resp.ok) return resp;

  let detail = "unknown_error";
  try {
    const body = await resp.clone().json();
    detail = body.detail ?? "unknown_error";
  } catch { /* no JSON body */ }

  const errorClass = classifyError(resp.status, detail);
  throw new AuthError(detail, errorClass, resp.status);
}
```

### Subtle case: `invalid_google_token` returns 401

By default the classifier says AUTH_ERROR → force logout. But this 401 comes from `/auth/oauth` (no session in progress, the user is just trying to log in).

**Rule from Section 3**: the 401 handler does not apply to the `/auth/refresh` and `/auth/oauth` paths. The error propagates to `auth-service.signIn()` which manually reclassifies it as VALIDATION_ERROR.

### Logs — strict PII rules

**Forbidden:**
- Access token, refresh token, idToken (even truncated)
- Email, display_name, password
- Local variables with user inputs

**Allowed:**
- Error codes (`code=invalid_credentials class=VALIDATION_ERROR`)
- Timings (`duration=842ms`)
- User ID (UUID, internal identifier, not PII)

```ts
// services/logger.ts
export const logger = {
  info: (event: string, data?: Record<string, unknown>) => console.log(`[INFO] ${event}`, sanitize(data)),
  warn: (event: string, data?: Record<string, unknown>) => console.warn(`[WARN] ${event}`, sanitize(data)),
  error: (event: string, err: unknown, data?: Record<string, unknown>) => {
    console.error(`[ERROR] ${event}`, err, sanitize(data));
    // future: Sentry.captureException(err, { tags: { event }, extra: sanitize(data) });
  },
};

const PII_KEYS = ["access_token", "refresh_token", "token", "idToken",
                  "email", "display_name", "password"];

function sanitize(data?: Record<string, unknown>) {
  if (!data) return data;
  const clean = { ...data };
  for (const k of PII_KEYS) delete clean[k];
  return clean;
}
```

### Sentry — V1 integration (wiring pending credentials)

Decided in session: Sentry will be activated in V1.

**Current state:**
- Backend: `sentry-sdk[fastapi]` installed in all services, `ratis_core.observability.init_sentry()` exists, `SENTRY_DSN=` in `.env.example` — **ready to receive the DSN**.
- Frontend: `@sentry/react-native` **not yet installed** — to be added via `npx expo install @sentry/react-native` + plugin config in `app.json`.

**RGPD config applied (non-negotiable):**
```
send_default_pii: false        # no email, IP, local variables
environment: "production" | "staging" | "development"
traces_sample_rate: 0.1        # 10% transactions
beforeSend: sanitize(...)      # residual PII scrubber
```

**To do as soon as DSNs are available:**
- [ ] Create 2 Sentry projects: `ratis-backend` (Python/FastAPI) + `ratis-mobile` (React Native/Expo)
- [ ] Retrieve the 2 DSNs
- [ ] Add `SENTRY_DSN=...` to `.env.local` of each backend service
- [ ] Install `@sentry/react-native` + plugin config `app.json`
- [ ] Wire `logger.error()` → `Sentry.captureException()` in prod
- [ ] Archive DP-02 in DECISIONS_ACTED.md

### UI behavior by family

| Family | UI |
|---|---|
| NETWORK_ERROR | Non-blocking toast, "Retry" button, no navigation |
| TIMEOUT | Same as NETWORK_ERROR |
| AUTH_ERROR | Navigate `/(auth)/login` + "Session expired" banner if force-logout |
| VALIDATION_ERROR | Inline message under field OR toast, stays on screen |
| SERVER_ERROR | Toast "An error occurred", "Retry" option |
| CANCELLED | Nothing. Natural return to previous screen |

### React error boundary

```tsx
<AuthProvider>
  <ErrorBoundary fallback={<AppCrashScreen />}>
    <Slot />
  </ErrorBoundary>
</AuthProvider>
```

ErrorBoundary inside AuthProvider — if the Provider crashes, we force a manual app restart (rare, recoverable).

### Rule "no error without exit path"

Every displayed error must offer a recovery path (Retry / Fix / Back). Never a blank screen or frozen app.

---

## Router guards & navigation

### Route structure

expo-router (file-based) with logical groups:

```
app/
├── _layout.tsx              # Root — AuthProvider, ErrorBoundary, AuthGate
├── (auth)/
│   ├── _layout.tsx          # Auth group
│   └── login.tsx
├── (tabs)/
│   ├── _layout.tsx          # Authenticated app group
│   ├── scan.tsx
│   ├── liste.tsx
│   ├── produit.tsx
│   └── profil.tsx
└── modal.tsx
```

The `(auth)` and `(tabs)` groups are not in the URL — public URL = `/login`, `/scan`, etc.

### Central guard — `app/_layout.tsx`

```tsx
function AuthGate() {
  const auth = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const inAuthGroup = segments[0] === "(auth)";

  useEffect(() => {
    if (auth.status === "initializing") return;

    if (auth.status === "authenticated" && inAuthGroup) {
      router.replace("/(tabs)/scan");
    } else if (auth.status !== "authenticated" && !inAuthGroup) {
      router.replace("/(auth)/login");
    }
  }, [auth.status, segments]);

  if (auth.status === "initializing") return <SplashScreen />;
  return <Slot />;
}
```

### Rule `replace` vs `push` — CRITICAL

**`router.replace()` ONLY at the 3 auth transitions:**
- Boot: initial route (login or home) based on auth state
- Successful login: `/(auth)/login` → `/(tabs)/scan`
- Force logout or signOut: anywhere → `/(auth)/login`

**`router.push()` / `<Link>` EVERYWHERE ELSE** in the authenticated app:
- `/(tabs)/scan` → `/(tabs)/produit/789`
- `/(tabs)/liste` → `/(tabs)/liste/edit/abc`
- All intra-app navigation

**UX consequence:**
- Android back button and iOS swipe work normally in `/(tabs)/*` — full history is preserved
- Cannot go back to `/login` after successful auth (coherent)
- Cannot go back to a protected screen after force logout (coherent)

### Why this pattern

| Choice | Reason |
|---|---|
| Guard at root level | Single place to maintain, no duplication per screen |
| `replace` at auth transitions | Avoids loops (logout → back → protected screen) |
| `push` inside | Preserves native back button, smooth UX |
| `useEffect` for redirect | Official expo-router pattern, no conditional rendering |
| Gating on `initializing` | No login screen flash that disappears 200ms later |

### Native splash

```tsx
import * as SplashScreen from "expo-splash-screen";

SplashScreen.preventAutoHideAsync();

function AuthGate() {
  const auth = useAuth();
  useEffect(() => {
    if (auth.status !== "initializing") SplashScreen.hideAsync();
  }, [auth.status]);
  // ...
}
```

Expo splash stays displayed until we hide it — no blank frame at startup.

### Deep links (V1: out of scope, pattern documented)

```tsx
function AuthGate() {
  const [pendingIntent, setPendingIntent] = useState<string | null>(null);

  useEffect(() => {
    if (auth.status === "initializing") return;

    if (auth.status !== "authenticated" && !inAuthGroup) {
      setPendingIntent(pathname); // memorize target URL
      router.replace("/(auth)/login");
    } else if (auth.status === "authenticated") {
      if (pendingIntent) {
        router.replace(pendingIntent);
        setPendingIntent(null);
      } else if (inAuthGroup) {
        router.replace("/(tabs)/scan");
      }
    }
  }, [auth.status, segments]);
}
```

### Login screen — UI

```tsx
export default function LoginScreen() {
  const auth = useAuth();
  const [appleAvailable, setAppleAvailable] = useState(false);

  useEffect(() => {
    AppleAuth.isAvailableAsync().then(setAppleAvailable);
  }, []);

  const signingIn = auth.status === "authenticating";

  return (
    <View style={styles.container}>
      <Image source={require("@/assets/logo.png")} style={styles.logo} />
      <Text style={styles.tagline}>{t("auth.tagline")}</Text>

      {appleAvailable && (
        <AppleAuth.AppleAuthenticationButton
          buttonType={AppleAuth.AppleAuthenticationButtonType.CONTINUE}
          buttonStyle={AppleAuth.AppleAuthenticationButtonStyle.BLACK}
          cornerRadius={12}
          style={styles.appleButton}
          onPress={() => auth.signIn("apple")}
        />
      )}

      <GoogleButton
        onPress={() => auth.signIn("google")}
        disabled={signingIn}
        loading={signingIn && auth.provider === "google"}
      />

      {auth.status === "unauthenticated" && auth.error && (
        <ErrorBanner message={t(`auth.${auth.error.code}`)} />
      )}

      <LegalFooter />
    </View>
  );
}
```

**UI rules:**
1. Buttons disabled during signIn (prevents double-tap)
2. Spinner on pressed button only (no fullscreen modal)
3. Apple Sign-In: use official `AppleAuthenticationButton` (App Store guideline)
4. Google: follow [Google Branding Guidelines](https://developers.google.com/identity/branding-guidelines)
5. Legal footer mandatory (Terms + Privacy — App Store + RGPD)

### Legal notices

**Chosen domain: `ratis.app`.**

Rationale:
- `.app` TLD managed by Google Registry, forces HTTPS via HSTS preload (enhanced perceived security)
- Signals mobile-first positioning
- Price comparable to `.fr` / `.com`

Legal links (in login footer) point to external pages:
- `https://ratis.app/legal/cgu`
- `https://ratis.app/legal/confidentialite`

**Centralized constant** to allow easy domain changes:

```ts
// constants/Legal.ts
export const LEGAL_URLS = {
  cgu: "https://ratis.app/legal/cgu",
  privacy: "https://ratis.app/legal/confidentialite",
  support: "https://ratis.app/support",
} as const;
```

**If the domain changes later** (ratis.fr, ratis.io, etc.), modification in 1 single file + 301 redirect server-side from the old domain. No app modification needed. See `DA-27` in DECISIONS_ACTED.md.

### Transitions & animations

- `/(auth)/login` → `/(tabs)/scan`: fade 200ms
- `/(tabs)/*` → `/(auth)/login`: fade 200ms + stack reset

Configured in the respective `_layout.tsx`.

### Navigation persistence

expo-router persists the current route. User closes the app on `/(tabs)/produit/789` → on re-open returns there if still authenticated. Otherwise (refresh expired after 30d), AuthGate redirects to `/(auth)/login`.

---

## Tests

### Stack

```
jest + jest-expo preset
@testing-library/react-native
@testing-library/jest-native
msw (Mock Service Worker for fetch)
```

### Target pyramid

- **Unit (30-40 tests)** — reducer, api-client, token-storage, waitForOnline, auth-service
- **Component (15-20 tests)** — LoginScreen, AuthGate
- **Integration (10-15 tests)** — end-to-end flow with msw + in-memory stores
- **E2E (Detox)** — deferred to V1.1

### Critical modules — coverage ≥90%

**`authReducer.test.ts`** (~12 tests): all state machine transitions from each valid initial state.

**`token-storage.test.ts`** (~8 tests): set/get/clear, PII invariants (never log), SecureStore error handling.

**`api-client.test.ts`** (~15 tests) — **THE most critical**:
- Authorization header injection
- 4xx parsing → AuthError with detailed code
- 401 handling: refresh + retry ONCE, path exclusions (`/auth/refresh`, `/auth/oauth`)
- Concurrent refresh: serialization via Promise singleton
- Network errors: waitForOnline + retry after network returns
- Timeouts: AbortController 15s refresh / 30s default
- Emit `force_logout` event when refresh 401/403

**`auth-service.test.ts`** (~10 tests): Apple/Google signIn, operation order, resilience between steps, SHA-256 nonce.

**`waitForOnline.test.ts`** (~6 tests): event-driven resolve, timeout, unsubscribe (no memory leak), online/offline oscillations.

### UI modules — coverage 60-70%

**`LoginScreen.test.tsx`** (~10 tests): Apple visibility per iOS/Android, buttons disabled during signIn, spinner on pressed button only, error banner per errorClass, i18n compliance.

**`AuthGate.test.tsx`** (~8 tests): SplashScreen during init, auth/non-auth redirects, deep link intent preservation, no flash.

### Integration — `auth-flow.integration.test.tsx`

Render full `<AuthProvider>`, mock only:
- OAuth SDK (fake signed idToken)
- Backend via msw (realistic JSON)
- SecureStore (in-memory Map)

Scenarios:
- Sign in Google → land on tabs
- Sign out → return to login
- Session expired during use → force logout + banner
- Network drop during login → retry after return → success
- User cancels OAuth prompt → stays on login, no error

### Centralized mocks (`__mocks__/`)

```
expo-secure-store.ts        → In-memory Map
expo-apple-authentication.ts → isAvailableAsync + signInAsync stubs
expo-auth-session.ts        → AuthRequest.promptAsync stubs
expo-network.ts             → getNetworkStateAsync + listener control
expo-localization.ts        → timezone="Europe/Paris"
expo-crypto.ts              → digestStringAsync passthrough
```

### Fixtures (`tests/fixtures/`)

```
users.ts       # mockUser
tokens.ts      # mockValidTokens, mockExpiredTokens
responses.ts   # mockOAuthResponse, mockMeResponse, mock401, mockValidationError
msw-handlers.ts # Default handlers
```

### Frontend CI — to be created

New workflow `.github/workflows/ratis_client.yml`:
- Install deps
- `tsc --noEmit`
- Lint
- `jest --coverage --ci`
- Upload Codecov

Aligned with backend pattern (`ratis_auth.yml`, etc.).

### TDD order (= implementation order of the plan)

1. `authReducer` (most isolated state)
2. `token-storage` (dependency for everything else)
3. `waitForOnline` (network utility)
4. `api-client` step by step
5. `auth-service`
6. `AuthContext` + `AuthProvider` + `useAuth`
7. `AuthGate`
8. `LoginScreen`
9. Integration tests

**Golden rule:** test → code → atomic commit. Never commit code without a test covering it (CLAUDE.md).

---

## Parameters

**Environment variables (`.env.local`):**
- `GOOGLE_CLIENT_ID_IOS` — iOS OAuth client ID (Google Cloud Console)
- `GOOGLE_CLIENT_ID_ANDROID` — Android OAuth client ID
- `APPLE_SERVICES_ID` — Apple Developer Services ID (for web fallback, n/a on native iOS)

**Timeouts (constants in `services/api-client.ts`):**
```ts
export const TIMEOUTS = {
  REFRESH_TOKEN: 15_000,      // /auth/refresh — hard gate
  WAIT_FOR_ONLINE: 15_000,    // event-driven max wait
  DEFAULT_REQUEST: 30_000,    // other endpoints
  UPLOAD: 60_000,             // image uploads (scan receipt)
  UI_SLOW_FEEDBACK_MS: 3_000, // "connexion lente..."
  UI_VERY_SLOW_FEEDBACK_MS: 8_000, // "prend plus de temps..."
};
```

---

## Rules

- **OAuth-only** in V1. No email/password login screen on the app side.
- **SecureStore only** for tokens. Never AsyncStorage, never Zustand persist, never a global JS variable.
- **Access token never logged.** Even truncated. Even in dev.
- **Refresh token never sent** anywhere other than `/auth/refresh`.
- **Logout only on explicit 401 from the backend.** Timeout / 5xx / network error → UI error, no logout.
- **One retry maximum** after refresh. No loop.
- **Refresh serialized** via Promise singleton. Never concurrent refreshes.
- **Network pre-check forbidden** before fetch. Always attempt, trust the result. `NetInfo` is only used for decorative feedback.
- **i18n mandatory** on all user-facing error strings (keys `auth.*`).

---

## Out of scope

- Email/password in frontend (deferred to V2 — requires email infrastructure)
- Forgot password (deferred to V2 — requires email infrastructure)
- 2FA / MFA (not V1, OAuth providers handle their own 2FA)
- Magic links (not V1)
- Account linking UI (merging 2 OAuth accounts for the same user) — handled backend-side automatically via email lookup, no V1 UI
- Account deletion from the app (backend exists, UI in separate Account spec)
- Subscription / Stripe (separate Account spec)
- Auth analytics (conversion rates, etc. — post-V1)
