// ratis_client/__mocks__/expo-secure-store.ts

let store: Map<string, string> = new Map();

export async function setItemAsync(key: string, value: string): Promise<void> {
  store.set(key, value);
}

export async function getItemAsync(key: string): Promise<string | null> {
  return store.get(key) ?? null;
}

export async function deleteItemAsync(key: string): Promise<void> {
  store.delete(key);
}

export function __reset(): void {
  store = new Map();
}

// Constants used by the real module
export const WHEN_UNLOCKED = "WHEN_UNLOCKED";
export const WHEN_UNLOCKED_THIS_DEVICE_ONLY = "WHEN_UNLOCKED_THIS_DEVICE_ONLY";
