// ratis_client/services/token-storage.ts

import * as SecureStore from "expo-secure-store";
import { StoredTokens } from "@/types/auth";

const KEY_ACCESS = "ratis.auth.access_token";
const KEY_REFRESH = "ratis.auth.refresh_token";
const KEY_EXPIRES = "ratis.auth.expires_at";

const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export const tokenStorage = {
  async set(tokens: StoredTokens): Promise<void> {
    if (!tokens.accessToken || !tokens.refreshToken) {
      throw new Error("invalid_tokens");
    }
    await SecureStore.setItemAsync(KEY_ACCESS, tokens.accessToken, OPTIONS);
    await SecureStore.setItemAsync(KEY_REFRESH, tokens.refreshToken, OPTIONS);
    await SecureStore.setItemAsync(KEY_EXPIRES, String(tokens.expiresAt), OPTIONS);
  },

  async get(): Promise<StoredTokens | null> {
    const accessToken = await SecureStore.getItemAsync(KEY_ACCESS);
    const refreshToken = await SecureStore.getItemAsync(KEY_REFRESH);
    const expiresStr = await SecureStore.getItemAsync(KEY_EXPIRES);
    if (!accessToken || !refreshToken || !expiresStr) return null;
    return { accessToken, refreshToken, expiresAt: Number(expiresStr) };
  },

  async getAccess(): Promise<string | null> {
    return SecureStore.getItemAsync(KEY_ACCESS);
  },

  async getRefresh(): Promise<string | null> {
    return SecureStore.getItemAsync(KEY_REFRESH);
  },

  async clear(): Promise<void> {
    await SecureStore.deleteItemAsync(KEY_ACCESS);
    await SecureStore.deleteItemAsync(KEY_REFRESH);
    await SecureStore.deleteItemAsync(KEY_EXPIRES);
  },
};
