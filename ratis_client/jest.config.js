const path = require('path');

module.exports = {
  preset: 'jest-expo',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  // Default 5s is too tight on self-hosted runners running with --coverage.
  // Heavy screens (scan-history with useInfiniteQuery + FlatList + camera perms)
  // legitimately take 6-9s on CI while passing locally in <1s. Bump to 15s —
  // `findByText` retries up to ~4.5s by default so we keep headroom.
  testTimeout: 15000,
  // jest-expo's default env (react-native) forces a `react-native` export condition.
  // msw v2 returns null for that condition, breaking `msw/node`. Use plain `node`
  // env so package `exports` resolve via `node`/`require`.
  testEnvironment: 'node',
  testEnvironmentOptions: {
    customExportConditions: ['node', 'node-addons', 'require', 'default'],
  },
  moduleNameMapper: {
    '^@/(.+)\\.svg$': '<rootDir>/__mocks__/svgMock.tsx',
    '^@/(.*)$': '<rootDir>/$1',
    '\\.svg': '<rootDir>/__mocks__/svgMock.tsx',
    '^@react-native-async-storage/async-storage$': '<rootDir>/__mocks__/async-storage.js',
    // Skia is a native C++ module — won't load in Node test env. Mock with
    // passthrough React components so the tree mounts.
    '^@shopify/react-native-skia$': '<rootDir>/__mocks__/shopify-react-native-skia.tsx',
    // MapLibre ships native modules (TurboModule registry) absent under the
    // Node test env. Passthrough mock surfaces the props RouteMap renders.
    '^@maplibre/maplibre-react-native$':
      '<rootDir>/__mocks__/maplibre-react-native.tsx',
  },
  // Coverage floor — set a few points UNDER the measured baseline (2026-06-24:
  // stmts 85.99 / branch 79.79 / funcs 80.4 / lines 87.82, 1167 tests) so a
  // normal `npx jest --coverage` stays green while still catching a real drop.
  // Raise these as coverage climbs (ratchet, don't loosen).
  coverageThreshold: {
    global: {
      statements: 82,
      branches: 75,
      functions: 76,
      lines: 83,
    },
  },
  moduleDirectories: ['node_modules', '<rootDir>'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'json', 'node'],
  // jest-expo only registers babel-jest for .[jt]sx? — extend to .mjs so that
  // ESM-only packages in node_modules (e.g. `rettime` pulled by msw) get parsed.
  transform: {
    '\\.mjs$': ['babel-jest', {
      caller: { name: 'metro', bundler: 'metro', platform: 'ios' },
      configFile: require.resolve('expo/internal/babel-preset.js'),
    }],
  },
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?|@react-native/)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg|react-native-reanimated|react-native-gesture-handler|react-native-worklets|react-native-safe-area-context|react-native-screens|@react-native-async-storage|expo-task-manager|expo-background-fetch|msw|@mswjs|@bundled-es-modules|until-async|@open-draft|outvariant|is-node-process|strict-event-emitter|headers-polyfill|rettime|cookie|tough-cookie|set-cookie-parser)',
  ],
};
