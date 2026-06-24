---
name: eas-keychain-release-command-discipline
description: "Run EAS OTA updates and builds non-interactively using the Expo token stored in Keychain, with matching channel/environment flags and end-to-end completion verification instead of stopping after launch."
---

# eas-keychain-release-command-discipline

`eas update` / `eas build` fails when no Expo account is logged in, and
running them from automation requires a non-interactive token. On Ratis
the token lives in Keychain (service `ratis-agent-mcp`, account `eas`).
The discipline : inject the token without echoing it, pass the
non-interactive flags with **matching** `--channel` / `--environment`
(KP-57), then verify the update/build reached a terminal success state —
launching the command is not the same as it finishing.

This skill is the command-execution companion to the deploy *policy* in
`CLAUDE.md` § EAS / mobile deploy and R34 (pre-publish gate, channel
matching, OTA-vs-rebuild). Follow that policy; this skill is how you run
the commands cleanly once the gate passes.

## When to Use

- `eas update` / `eas build` fails because no Expo account is logged in.
- An OTA update or build must run from automation / a background process,
  non-interactively.

## When NOT to Use

- The pre-publish gate (R34) is not satisfied — clean tree + `HEAD ==
  origin/main`, publish only after PR merge. Fix the gate first.
- A native change that requires a rebuild is being treated as an OTA, or
  vice versa — decide OTA-vs-rebuild per R34 before picking the command.
- You can do it through the agent-mcp `eas_*` tools (e.g.
  `eas_update_production`) — those already handle the token and are
  preferable to a raw CLI call.

## Procedure

1. **Pull the token into env without echoing it.** Read the Expo token
   from Keychain (service `ratis-agent-mcp`, account `eas`) into
   `EXPO_TOKEN` ; never print it to stdout / logs.
2. **Confirm the target channel first.** `eas build:list --limit 1
   --platform=android` → read `Channel:` so the update lands on the
   channel the installed APK actually listens to (KP-32).
3. **Run with matching flags, non-interactive.** Invoke with `EXPO_TOKEN`
   set and `--channel X --environment X` matching each other (KP-57). For
   builds, use the appropriate CI / non-interactive flags so it never
   prompts.
4. **Verify the terminal state.** Capture the build/update URL and poll
   until it reports a real success state — do not report "done" after the
   command merely launched. A killed or still-running build is not a
   shipped build.
5. **Recovery.** A bad OTA rolls back with `eas update:roll-back-to-embedded
   --channel X` (no rebuild needed).
