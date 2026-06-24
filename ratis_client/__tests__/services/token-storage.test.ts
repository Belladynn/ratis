// ratis_client/__tests__/services/token-storage.test.ts

jest.mock("expo-secure-store");

import { tokenStorage } from "@/services/token-storage";
import * as SecureStore from "expo-secure-store";

// Cast to access mock reset
const mockStore = SecureStore as unknown as { __reset: () => void };

describe("tokenStorage", () => {
  beforeEach(() => mockStore.__reset());

  it("get returns null when nothing stored", async () => {
    await expect(tokenStorage.get()).resolves.toBeNull();
  });

  it("set then get returns stored tokens", async () => {
    await tokenStorage.set({
      accessToken: "a",
      refreshToken: "r",
      expiresAt: 12345,
    });
    await expect(tokenStorage.get()).resolves.toEqual({
      accessToken: "a",
      refreshToken: "r",
      expiresAt: 12345,
    });
  });

  it("clear removes stored tokens", async () => {
    await tokenStorage.set({ accessToken: "a", refreshToken: "r", expiresAt: 1 });
    await tokenStorage.clear();
    await expect(tokenStorage.get()).resolves.toBeNull();
  });

  it("getAccess returns only access token", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    await expect(tokenStorage.getAccess()).resolves.toBe("A");
  });

  it("getRefresh returns only refresh token", async () => {
    await tokenStorage.set({ accessToken: "A", refreshToken: "R", expiresAt: 1 });
    await expect(tokenStorage.getRefresh()).resolves.toBe("R");
  });

  it("getAccess returns null when nothing stored", async () => {
    await expect(tokenStorage.getAccess()).resolves.toBeNull();
  });

  it("set rejects when refreshToken is empty", async () => {
    await expect(
      tokenStorage.set({ accessToken: "a", refreshToken: "", expiresAt: 1 })
    ).rejects.toThrow("invalid_tokens");
  });

  it("set rejects when accessToken is empty", async () => {
    await expect(
      tokenStorage.set({ accessToken: "", refreshToken: "r", expiresAt: 1 })
    ).rejects.toThrow("invalid_tokens");
  });
});
