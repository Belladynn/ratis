// ratis_client/services/build-info.ts
//
// OTA build number — bumped manually before each `eas update --channel production`
// push. Visible in the profil tab footer so users can verify whether a fresh
// bundle has actually been applied on their device after force-stop ×2.
//
// Sister field in `_layout.tsx` boot Sentry event so we can also confirm
// server-side (no need to ask the user to read a number on screen).
//
// Bump rule : every time we open a PR that ships an OTA-eligible change to
// the client, increment OTA_BUILD by 1 in the same commit. Skipped only for
// changes that don't actually go out via OTA (server-only, doc-only, etc.).
//
// History :
//   1 — 2026-04-27 — initial introduction (PR #138 / #139)
//   2 — 2026-04-27 — version trio display (semver + native build + OTA)
//   3 — 2026-04-30 — scan-history pen+date (PR #178) + store validation modal (PR #181)
//   4 — 2026-04-30 — re-push via ota-push.sh post-deploy (visual marker)
//   5 — 2026-04-30 — UPPERCASE normalize OCR-derived display (modal + accordions)
//   6 — 2026-05-01 — APK rebuild (SDK 54.0.33) + scan-history v3 statuses + display_name OFF multi-fields
//   7 — 2026-05-01 — profil SupportIdCard (RTS-XXXXXX) avec copy (PR #235)        [const not bumped at the time]
//   8 — 2026-05-01 — scan-history consensus_state badges (PR #242)                [const not bumped]
//   9 — 2026-05-01 — scan-history heure + badge "Traitement en cours" + auto-refresh (PR #243) [const not bumped]
//  10 — 2026-05-02 — vert = consensus only + preview scan-history filtre 10min orphans (PRs #245/#246) [const not bumped]
//  11 — 2026-05-05 — strict iso V5 Claude Design — 4 écrans + 2 modales + tab bar (PR #288 first push) [const not bumped]
//  12 — 2026-05-05 — re-publish with EAS env vars inlined (KP-57 — --environment preview fix) [const not bumped]
//  13 — 2026-05-05 — Visual iso V5 reconstruction — full visual reset, business logic intact (PR #300)
//                    Catch-up note: const drift detected 2026-05-05 — bump 6→13 catches up the 6 missed OTAs (entries 7-12).

export const OTA_BUILD = 13;
