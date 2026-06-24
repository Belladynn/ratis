// ratis_client/types/auth.ts

export type User = {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  account_type: string;
  timezone: string;
  current_level_id: string | null;
};

export type ErrorClass =
  | "NETWORK_ERROR"
  | "TIMEOUT"
  | "AUTH_ERROR"
  | "VALIDATION_ERROR"
  | "SERVER_ERROR"
  | "CANCELLED";

export class AuthError extends Error {
  constructor(
    public readonly code: string,
    public readonly errorClass: ErrorClass,
    public readonly httpStatus?: number,
    public readonly details?: unknown,
  ) {
    super(code);
    this.name = "AuthError";
  }
}

export type AuthState =
  | { status: "initializing" }
  | { status: "unauthenticated"; error: AuthError | null }
  | { status: "authenticating"; provider: "apple" | "google" }
  // `user` is null on an optimistic offline boot (tokens valid but the
  // /auth/me connectivity check could not complete). Screen data is sourced
  // from React Query (`useAuthMe`) which refetches once connectivity returns.
  | { status: "authenticated"; user: User | null };

export type AuthAction =
  | { type: "BOOT_SUCCESS"; user: User }
  | { type: "BOOT_FAIL" }
  // Optimistic boot: stored tokens are present but the network was down so
  // /auth/me could not be verified. Keep the user signed in rather than
  // ejecting a valid session.
  | { type: "BOOT_OFFLINE" }
  | { type: "SIGNIN_START"; provider: "apple" | "google" }
  | { type: "SIGNIN_SUCCESS"; user: User }
  | { type: "SIGNIN_FAIL"; error: AuthError }
  | { type: "SIGNOUT" }
  | { type: "FORCE_LOGOUT" };

export type StoredTokens = {
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // epoch ms
};
