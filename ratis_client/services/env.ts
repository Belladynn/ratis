// ratis_client/services/env.ts
//
// Read a required `EXPO_PUBLIC_*` env var. Throws loudly if it's missing
// OR empty — no silent hardcoded fallback. A missing env var is a CONFIG
// BUG that must be fixed in EAS, not papered over in code.
//
// Why the explicit empty-string check : Metro bakes `process.env.EXPO_PUBLIC_*`
// as a string literal at bundle time. When the EAS env var is unset, the
// Metro substitution produces `undefined`. When it's set to "", it produces
// the literal "". Native `??` only catches the null/undefined case, NOT "".
//
// Lesson 2026-04-26 alpha : OTA bundle shipped with `EXPO_PUBLIC_API_URL=""`
// (env vars not declared in the EAS Update environment scope), `?? fallback`
// did not fire, `BASE_URL` ended up `""`, every fetch failed with "Network
// request failed", login impossible. A silent fallback to a hardcoded URL
// would have hidden the same root cause and routed dev/preview traffic to
// prod servers without anyone noticing — strictly worse.
export function requireEnv(name: string, value: string | undefined): string {
  if (value === undefined || value === "") {
    throw new Error(
      `Missing required EAS env var: ${name}. Configure it via ` +
        `\`eas env:create --environment <preview|production> --name ${name} ` +
        `--value <url> --visibility plaintext\` then republish.`,
    );
  }
  return value;
}
