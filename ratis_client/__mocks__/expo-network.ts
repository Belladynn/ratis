// ratis_client/__mocks__/expo-network.ts

type Listener = (state: { isConnected: boolean }) => void;

let currentState: { isConnected: boolean } = { isConnected: true };
let listeners: Set<Listener> = new Set();

export enum NetworkStateType {
  WIFI = "WIFI",
  CELLULAR = "CELLULAR",
  UNKNOWN = "UNKNOWN",
  NONE = "NONE",
}

export async function getNetworkStateAsync() {
  return { ...currentState, type: NetworkStateType.WIFI };
}

export function addNetworkStateListener(listener: Listener) {
  listeners.add(listener);
  return { remove: () => listeners.delete(listener) };
}

// Test helpers
export function __setNetworkState(state: { isConnected: boolean }) {
  currentState = state;
  for (const l of listeners) l(state);
}

export function __reset() {
  currentState = { isConnected: true };
  listeners = new Set();
}
