// Jest setup for path aliases and i18n.
//
// EXPO_PUBLIC_* env vars are intentionally NOT stubbed here. `requireEnv`
// (services/env.ts) throws when a var is missing — that's by design (R33).
// Tests that exercise paths reading those vars must stub them in their own
// setup using `process.env.EXPO_PUBLIC_FOO = '...'` (cf scan-queue.test.ts).
// Tests that hit `apiClient`/`useAccountStats` etc. without stubbing get a
// fast-failing query (queryFn throws synchronously, isLoading transitions
// to false) which matches what real misconfigured bundles would surface.

// waitFor / findBy* default to a 1s `asyncUtilTimeout`, INDEPENDENT of jest's
// `testTimeout` (15s). On the self-hosted Mac mini (16 runners + Hermes +
// GlitchTip sharing the box) async query resolution can exceed 1s under load,
// causing load-induced flakes (e.g. `useProductByEan › error state on 404`,
// `card.test.tsx` — both pass locally in <100ms). Bump to 5s: waitFor still
// returns as soon as the condition holds, so passing tests are NOT slowed —
// only the give-up ceiling moves, killing the false reds.
const { configure } = require('@testing-library/react-native');
configure({ asyncUtilTimeout: 5000 });

// Mock expo-localization so i18n init resolves to French in tests.
jest.mock('expo-localization', () => ({
  getCalendars: () => [{ timeZone: 'Europe/Paris' }],
  getLocales: () => [{ languageCode: 'fr' }],
}));

// Mock expo-image-manipulator — services/image-pipeline.ts uses it to
// flatten EXIF + resize captured photos. Tests don't care about the
// pixel data, only that the URI flows through, so we passthrough.
jest.mock('expo-image-manipulator', () => ({
  manipulateAsync: jest.fn(async (uri) => ({ uri, width: 1600, height: 2133 })),
  SaveFormat: { JPEG: 'jpeg', PNG: 'png' },
}));

// Mock expo-file-system/legacy — services/image-pipeline.ts and
// services/scan-queue.ts call `getInfoAsync` for diagnostic breadcrumbs only
// (they don't read/write content). Default to "file exists, 1234 bytes".
// Tests that need to drive a different result reach into the mock directly.
jest.mock('expo-file-system/legacy', () => ({
  getInfoAsync: jest.fn(async (uri) => ({
    exists: true,
    uri,
    size: 1234,
    isDirectory: false,
    // Legacy API returns seconds (not ms) — our breadcrumbs convert to ms.
    modificationTime: 1700000000,
  })),
}));

// Global mock for @react-native-google-signin/google-signin.
// AuthContext.tsx now uses GoogleSignin.signIn() at sign-in time. The default
// mocked response is "cancel" — tests that need to drive a different result
// call `global.__setNextGoogleResult({...})` before triggering sign-in.
//
// Allowed shapes:
//   { type: 'success', data: { idToken: 'fake.jwt', user: {...} } }
//   { type: 'cancelled', data: null }
//   Error instance (rejected from signIn)
global.__nextGoogleResult = { type: 'cancelled', data: null };
global.__setNextGoogleResult = (r) => {
  global.__nextGoogleResult = r;
};
global.__resetGoogleMock = () => {
  global.__nextGoogleResult = { type: 'cancelled', data: null };
};
jest.mock('@react-native-google-signin/google-signin', () => ({
  GoogleSignin: {
    configure: jest.fn(),
    hasPlayServices: jest.fn().mockResolvedValue(true),
    signIn: jest.fn(async () => {
      const r = global.__nextGoogleResult;
      if (r instanceof Error) throw r;
      return r;
    }),
    signOut: jest.fn().mockResolvedValue(null),
    revokeAccess: jest.fn().mockResolvedValue(null),
    getCurrentUser: jest.fn().mockReturnValue(null),
    hasPreviousSignIn: jest.fn().mockReturnValue(false),
  },
  isSuccessResponse: (r) => r && r.type === 'success',
  isCancelledResponse: (r) => r && r.type === 'cancelled',
  statusCodes: {
    SIGN_IN_CANCELLED: 'SIGN_IN_CANCELLED',
    IN_PROGRESS: 'IN_PROGRESS',
    PLAY_SERVICES_NOT_AVAILABLE: 'PLAY_SERVICES_NOT_AVAILABLE',
    SIGN_IN_REQUIRED: 'SIGN_IN_REQUIRED',
  },
}));

// Initialise i18n once globally — real translation files, no mock.
// Components calling t('key') resolve to FR values matching test assertions.
require('@/lib/i18n');
