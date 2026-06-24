// ratis_client/__tests__/contexts/authReducer.test.ts

import { authReducer, initialState } from "@/contexts/authReducer";
import { AuthError, AuthState, User } from "@/types/auth";

const mockUser: User = {
  id: "u-1",
  email: "x@y.z",
  display_name: "Test",
  avatar_url: null,
  account_type: "oauth",
  timezone: "Europe/Paris",
  current_level_id: null,
};

describe("authReducer", () => {
  it("initial state is initializing", () => {
    expect(initialState).toEqual({ status: "initializing" });
  });

  describe("from initializing", () => {
    const from: AuthState = { status: "initializing" };

    it("BOOT_SUCCESS → authenticated", () => {
      expect(authReducer(from, { type: "BOOT_SUCCESS", user: mockUser }))
        .toEqual({ status: "authenticated", user: mockUser });
    });

    it("BOOT_FAIL → unauthenticated with null error", () => {
      expect(authReducer(from, { type: "BOOT_FAIL" }))
        .toEqual({ status: "unauthenticated", error: null });
    });

    it("BOOT_OFFLINE → authenticated optimistically with null user", () => {
      expect(authReducer(from, { type: "BOOT_OFFLINE" }))
        .toEqual({ status: "authenticated", user: null });
    });
  });

  describe("from unauthenticated", () => {
    const from: AuthState = { status: "unauthenticated", error: null };

    it("SIGNIN_START → authenticating with provider", () => {
      expect(authReducer(from, { type: "SIGNIN_START", provider: "google" }))
        .toEqual({ status: "authenticating", provider: "google" });
    });
  });

  describe("from authenticating", () => {
    const from: AuthState = { status: "authenticating", provider: "google" };

    it("SIGNIN_SUCCESS → authenticated", () => {
      expect(authReducer(from, { type: "SIGNIN_SUCCESS", user: mockUser }))
        .toEqual({ status: "authenticated", user: mockUser });
    });

    it("SIGNIN_FAIL → unauthenticated with error", () => {
      const err = new AuthError("invalid_google_token", "VALIDATION_ERROR", 401);
      expect(authReducer(from, { type: "SIGNIN_FAIL", error: err }))
        .toEqual({ status: "unauthenticated", error: err });
    });
  });

  describe("from authenticated", () => {
    const from: AuthState = { status: "authenticated", user: mockUser };

    it("SIGNOUT → unauthenticated with null error", () => {
      expect(authReducer(from, { type: "SIGNOUT" }))
        .toEqual({ status: "unauthenticated", error: null });
    });

    it("FORCE_LOGOUT → unauthenticated with session_expired error", () => {
      const result = authReducer(from, { type: "FORCE_LOGOUT" });
      expect(result.status).toBe("unauthenticated");
      if (result.status === "unauthenticated") {
        expect(result.error?.code).toBe("session_expired");
        expect(result.error?.errorClass).toBe("AUTH_ERROR");
      }
    });
  });
});
