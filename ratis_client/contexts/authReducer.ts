// ratis_client/contexts/authReducer.ts

import { AuthAction, AuthError, AuthState } from "@/types/auth";

export const initialState: AuthState = { status: "initializing" };

export function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case "BOOT_SUCCESS":
      return { status: "authenticated", user: action.user };
    case "BOOT_FAIL":
      return { status: "unauthenticated", error: null };
    case "BOOT_OFFLINE":
      return { status: "authenticated", user: null };
    case "SIGNIN_START":
      return { status: "authenticating", provider: action.provider };
    case "SIGNIN_SUCCESS":
      return { status: "authenticated", user: action.user };
    case "SIGNIN_FAIL":
      return { status: "unauthenticated", error: action.error };
    case "SIGNOUT":
      return { status: "unauthenticated", error: null };
    case "FORCE_LOGOUT":
      return {
        status: "unauthenticated",
        error: new AuthError("session_expired", "AUTH_ERROR"),
      };
  }
}
