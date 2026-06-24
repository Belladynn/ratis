// ratis_client/__mocks__/@sentry/react-native.ts
// Jest mock — no real Sentry calls during tests

export const init = jest.fn();
export const captureException = jest.fn();
export const captureMessage = jest.fn();
export const setUser = jest.fn();
export const clearScope = jest.fn();
export const addBreadcrumb = jest.fn();
export const withScope = jest.fn(
  (fn: (scope: Record<string, unknown>) => void) => fn({}),
);
