// ratis_client/__mocks__/expo-auth-session.ts

type PromptResult =
  | { type: "success"; params: Record<string, string> }
  | { type: "cancel" }
  | { type: "error"; error?: unknown };

let nextResult: PromptResult = {
  type: "success",
  params: { id_token: "fake.google.jwt" },
};

export function makeRedirectUri(_opts: unknown): string {
  return "ratis://oauth";
}

export class AuthRequest {
  constructor(public readonly config: unknown) {}
  async promptAsync(_discovery: unknown): Promise<PromptResult> {
    return nextResult;
  }
}

// Test helpers
export function __setNextResult(r: PromptResult) { nextResult = r; }
export function __reset() {
  nextResult = { type: "success", params: { id_token: "fake.google.jwt" } };
}
