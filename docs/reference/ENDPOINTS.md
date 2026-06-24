# Ratis Endpoints Inventory

> **Auto-generated — do not edit manually.**
> Regenerate: `python scripts/generate-endpoints-inventory.py`
> CI enforces freshness on every PR (see `.github/workflows/doc-inventories.yml`).

**Agent rule (CLAUDE.md §3)**: run this script and read this file BEFORE any brainstorm or code session that may propose a new endpoint. Reuse existing endpoints rather than inventing duplicates.

**Columns** : Method · Path (with prefix applied) · Purpose (first-line docstring) · Source file.

## ratis_auth

| Method | Path | Purpose | Source |
|---|---|---|---|
| `DELETE` | `/api/v1/account` | — | `webservices/ratis_auth/routes/account.py` |
| `GET` | `/api/v1/account/identities` | List the OAuth identities linked to the current account | `webservices/ratis_auth/routes/account.py` |
| `DELETE` | `/api/v1/account/identities/{provider}` | Unlink an OAuth identity from the current account | `webservices/ratis_auth/routes/account.py` |
| `POST` | `/api/v1/account/link-provider` | Link an additional OAuth identity to the current account | `webservices/ratis_auth/routes/account.py` |
| `POST` | `/api/v1/account/logout` | — | `webservices/ratis_auth/routes/account.py` |
| `POST` | `/api/v1/account/logout-all` | — | `webservices/ratis_auth/routes/account.py` |
| `GET` | `/api/v1/account/preferences` | — | `webservices/ratis_auth/routes/account.py` |
| `PATCH` | `/api/v1/account/preferences` | — | `webservices/ratis_auth/routes/account.py` |
| `GET` | `/api/v1/account/profile` | — | `webservices/ratis_auth/routes/account.py` |
| `PATCH` | `/api/v1/account/profile` | — | `webservices/ratis_auth/routes/account.py` |
| `POST` | `/api/v1/account/rings/claim` | Break one pending ROI ring, if any | `webservices/ratis_auth/routes/account.py` |
| `GET` | `/api/v1/account/stats` | Return aggregated scan/saving stats for the Profil screen | `webservices/ratis_auth/routes/account.py` |
| `POST` | `/admin/session-bootstrap [UNMOUNTED?]` | Mint a 60-second single-use OTT and return the browser-ready URL | `webservices/ratis_auth/routes/admin/session_bootstrap.py` |
| `GET` | `/admin/users/{user_id}/subscription [UNMOUNTED?]` | Return the most-recent subscription row for ``user_id`` | `webservices/ratis_auth/routes/admin/subscription.py` |
| `PATCH` | `/admin/users/{user_id}/subscription/activate [UNMOUNTED?]` | Force-activate a subscription (manual grant, alpha/promo, support) | `webservices/ratis_auth/routes/admin/subscription.py` |
| `PATCH` | `/admin/users/{user_id}/subscription/deactivate [UNMOUNTED?]` | Cancel a subscription | `webservices/ratis_auth/routes/admin/subscription.py` |
| `PATCH` | `/admin/users/{user_id}/subscription/extend [UNMOUNTED?]` | Push ``expires_at`` forward (trial grace) | `webservices/ratis_auth/routes/admin/subscription.py` |
| `GET` | `/admin/users [UNMOUNTED?]` | Return a filtered, paginated user list (summary fields only) | `webservices/ratis_auth/routes/admin/users.py` |
| `GET` | `/admin/users/{user_id} [UNMOUNTED?]` | Return a full profile for one user, plus useful aggregates | `webservices/ratis_auth/routes/admin/users.py` |
| `GET` | `/api/v1/auth/me` | — | `webservices/ratis_auth/routes/auth.py` |
| `POST` | `/api/v1/auth/oauth` | — | `webservices/ratis_auth/routes/auth.py` |
| `POST` | `/api/v1/auth/refresh` | — | `webservices/ratis_auth/routes/auth.py` |
| `DELETE` | `/api/v1/account/subscription` | — | `webservices/ratis_auth/routes/subscription.py` |
| `GET` | `/api/v1/account/subscription` | — | `webservices/ratis_auth/routes/subscription.py` |
| `POST` | `/api/v1/account/subscription` | — | `webservices/ratis_auth/routes/subscription.py` |
| `POST` | `/webhooks/stripe` | — | `webservices/ratis_auth/routes/webhooks.py` |

## ratis_list_optimiser

| Method | Path | Purpose | Source |
|---|---|---|---|
| `POST` | `/api/v1/lists/{list_id}/optimize` | Queue route optimization for a shopping list | `webservices/ratis_list_optimiser/routes/optimization.py` |
| `GET` | `/api/v1/lists/{list_id}/route` | Get the latest non-expired route for a shopping list | `webservices/ratis_list_optimiser/routes/optimization.py` |
| `GET` | `/api/v1/price` | — | `webservices/ratis_list_optimiser/routes/optimization.py` |
| `GET` | `/api/v1/routes/{route_id}` | Get an optimized route by its ID | `webservices/ratis_list_optimiser/routes/optimization.py` |
| `POST` | `/api/v1/routes/{route_id}/move-item` | Move an item from one store to another within an optimized route | `webservices/ratis_list_optimiser/routes/optimization.py` |
| `POST` | `/api/v1/routes/{route_id}/remove-store` | Remove a store from the route and redistribute its items | `webservices/ratis_list_optimiser/routes/optimization.py` |
| `GET` | `/api/v1/lists` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `POST` | `/api/v1/lists` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `POST` | `/api/v1/lists/from-template/{template_id}` | Create a new list from a template | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `DELETE` | `/api/v1/lists/{list_id}` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `GET` | `/api/v1/lists/{list_id}` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `PATCH` | `/api/v1/lists/{list_id}` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `POST` | `/api/v1/lists/{list_id}/clear` | Remove all items from a list | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `POST` | `/api/v1/lists/{list_id}/items` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `DELETE` | `/api/v1/lists/{list_id}/items/{item_id}` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `PATCH` | `/api/v1/lists/{list_id}/items/{item_id}` | — | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `POST` | `/api/v1/lists/{list_id}/save-as-template` | Save a copy of a list as a template | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `POST` | `/api/v1/lists/{list_id}/scan-check` | Auto-check an item by scanning its barcode | `webservices/ratis_list_optimiser/routes/shopping_lists.py` |
| `GET` | `/api/v1/suggestions/eligibility` | — | `webservices/ratis_list_optimiser/routes/suggestions.py` |
| `POST` | `/api/v1/suggestions/generate` | — | `webservices/ratis_list_optimiser/routes/suggestions.py` |

## ratis_notifier

| Method | Path | Purpose | Source |
|---|---|---|---|
| `POST` | `/api/v1/notify` | Enqueue a push notification for a user | `webservices/ratis_notifier/routes/notify.py` |

## ratis_product_analyser

| Method | Path | Purpose | Source |
|---|---|---|---|
| `POST` | `/api/v1/admin/barcode/reparse` | Dispatch async re-parsing of receipts with raw barcode but no fields | `webservices/ratis_product_analyser/routes/admin/barcode.py` |
| `GET` | `/api/v1/admin/barcode/unknown-retailers` | Return retailers with raw barcodes but no parsed ``barcode_fields`` | `webservices/ratis_product_analyser/routes/admin/barcode.py` |
| `POST` | `/api/v1/admin/db-approvals` | Register a proposal reaching the human gate — INSERT a ``pending`` row | `webservices/ratis_product_analyser/routes/admin/db_approvals.py` |
| `POST` | `/api/v1/admin/db-approvals/{submission_id}/expire` | Mark a still-pending proposal as expired — n8n ``Wait`` timeout branch | `webservices/ratis_product_analyser/routes/admin/db_approvals.py` |
| `POST` | `/api/v1/admin/db-pipeline/apply-graduation` | M5 — mute ``app_settings.db_pipeline_trust_levels`` après une | `webservices/ratis_product_analyser/routes/admin/db_pipeline.py` |
| `POST` | `/api/v1/admin/db-pipeline/build-summary` | M3 — résumé français déterministe | `webservices/ratis_product_analyser/routes/admin/db_pipeline.py` |
| `POST` | `/api/v1/admin/db-pipeline/check-rowcount` | HSP4 M5 — confronte db_change_log au manifeste pour décider COMMIT|ROLLBACK | `webservices/ratis_product_analyser/routes/admin/db_pipeline.py` |
| `POST` | `/api/v1/admin/db-pipeline/compute-flags` | M4 — calcule les 5 anomaly flags structurels | `webservices/ratis_product_analyser/routes/admin/db_pipeline.py` |
| `POST` | `/api/v1/admin/db-pipeline/get-trust-level` | HSP3.1 — renvoie le trust level *effectif* d'une procédure | `webservices/ratis_product_analyser/routes/admin/db_pipeline.py` |
| `POST` | `/api/v1/admin/db-pipeline/validate-args` | HSP4 M3 — valide les args d'une proposition contre son manifeste HSP1 | `webservices/ratis_product_analyser/routes/admin/db_pipeline.py` |
| `GET` | `/api/v1/admin/receipts/{receipt_id}/debug` | Return debug payload for a receipt (PR #132) | `webservices/ratis_product_analyser/routes/admin/debug.py` |
| `GET` | `/api/v1/admin/scans/{scan_id}/debug` | Return debug payload for a scan (alpha instrumentation) | `webservices/ratis_product_analyser/routes/admin/debug.py` |
| `GET` | `/api/v1/admin/fraud_suspicions` | Browse the fraud_suspicions queue | `webservices/ratis_product_analyser/routes/admin/fraud_suspicions.py` |
| `GET` | `/api/v1/admin/fraud_suspicions/{suspicion_id}` | Return one fraud_suspicion enriched with the triggering receipt | `webservices/ratis_product_analyser/routes/admin/fraud_suspicions.py` |
| `PATCH` | `/api/v1/admin/fraud_suspicions/{suspicion_id}` | Mark a fraud_suspicion as ``confirmed_fraud`` / ``cleared`` / | `webservices/ratis_product_analyser/routes/admin/fraud_suspicions.py` |
| `GET` | `/api/v1/admin/knowledge/ocr-queue` | Read the OCR-knowledge curation queue | `webservices/ratis_product_analyser/routes/admin/knowledge.py` |
| `PATCH` | `/api/v1/admin/knowledge/{ocr_knowledge_id}` | Apply a manual correction or a dismissal on one ``ocr_knowledge`` row | `webservices/ratis_product_analyser/routes/admin/knowledge.py` |
| `GET` | `/api/v1/admin/name-resolutions/queue` | Paginated arbitration queue (UNVERIFIED first, then CONTROVERSE) | `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` |
| `POST` | `/api/v1/admin/name-resolutions/reject-challenges` | Re-promote the previously-verified EAN by appending ``manual_admin`` | `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` |
| `POST` | `/api/v1/admin/name-resolutions/resolve` | Tranche un cas controverse/unverified/unmatched en faveur de ``target_ean`` | `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` |
| `GET` | `/api/v1/admin/name-resolutions/unmatched` | Paginated scans without consensus and with stored fuzzy candidates | `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` |
| `GET` | `/api/v1/admin/name-resolutions/{store_id}/{normalized_label:path}` | Aggregate every ledger row + state-change event for a label | `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` |
| `POST` | `/api/v1/admin/name-resolutions/{store_id}/{normalized_label:path}/escalate` | Flag a label for priorisation manuel (audit-only, no side-effect) | `webservices/ratis_product_analyser/routes/admin/name_resolutions.py` |
| `GET` | `/api/v1/admin/parsed-tickets` | Browse parsed tickets, optionally filtered on derived status | `webservices/ratis_product_analyser/routes/admin/observability.py` |
| `GET` | `/api/v1/admin/parsed-tickets/{parsed_ticket_id}` | Aggregate one parsed ticket + linked scans + scoped audit events | `webservices/ratis_product_analyser/routes/admin/observability.py` |
| `POST` | `/api/v1/admin/parsed-tickets/{parsed_ticket_id}/replay` | Dispatch async re-run of Phase 3 + 4 on the persisted ParsedTicket | `webservices/ratis_product_analyser/routes/admin/observability.py` |
| `GET` | `/api/v1/admin/pipeline/audit-log` | Filter ``pipeline_audit_log`` rows for lineage debug | `webservices/ratis_product_analyser/routes/admin/observability.py` |
| `GET` | `/api/v1/admin/tasks/{task_id}/status` | Poll the status of a previously-dispatched Celery task | `webservices/ratis_product_analyser/routes/admin/observability.py` |
| `GET` | `/api/v1/admin/receipts/{receipt_id}` | Aggregate receipt + parsed_ticket + scans + audit + store | `webservices/ratis_product_analyser/routes/admin/scans.py` |
| `PATCH` | `/api/v1/admin/scans/{scan_id}` | Force-apply an admin correction on one scan | `webservices/ratis_product_analyser/routes/admin/scans.py` |
| `POST` | `/api/v1/admin/scans/{scan_id}/replay-match` | Re-run Phase 3 on one scan with the current DB knowledge | `webservices/ratis_product_analyser/routes/admin/scans.py` |
| `POST` | `/api/v1/admin/session-bootstrap` | Mint a 60-second single-use OTT and return the browser-ready URL | `webservices/ratis_product_analyser/routes/admin/session_bootstrap.py` |
| `GET` | `/api/v1/admin/pipeline/stats` | Aggregated pipeline stats for the given window | `webservices/ratis_product_analyser/routes/admin/stats.py` |
| `GET` | `/api/v1/admin/stores` | Browse stores filtered by validation status, retailer, location | `webservices/ratis_product_analyser/routes/admin/stores.py` |
| `POST` | `/api/v1/admin/stores/validate-bulk` | Atomically validate a list of stores | `webservices/ratis_product_analyser/routes/admin/stores.py` |
| `PATCH` | `/api/v1/admin/stores/{store_id}/disable` | Soft-delete a store. ``validation_status`` is left untouched | `webservices/ratis_product_analyser/routes/admin/stores.py` |
| `PATCH` | `/api/v1/admin/stores/{store_id}/geocode` | Set ``lat`` / ``lng`` on a store ; logs an ``admin_geocode`` row | `webservices/ratis_product_analyser/routes/admin/stores.py` |
| `PATCH` | `/api/v1/admin/stores/{store_id}/validate` | Force-confirm one store ; logs an ``admin_validate`` history row | `webservices/ratis_product_analyser/routes/admin/stores.py` |
| `GET` | `/api/v1/admin/users/{user_id}/scans` | Return a filtered, paginated scan list for one user | `webservices/ratis_product_analyser/routes/admin/users.py` |
| `GET` | `/api/v1/product/favorites` | — | `webservices/ratis_product_analyser/routes/product.py` |
| `GET` | `/api/v1/product/incomplete` | Return up to ``limit`` products with at least one missing field | `webservices/ratis_product_analyser/routes/product.py` |
| `GET` | `/api/v1/product/search` | Search the product catalogue by name + brand | `webservices/ratis_product_analyser/routes/product.py` |
| `GET` | `/api/v1/product/suggestions/default` | Return a tier-composed list of product suggestions for the empty | `webservices/ratis_product_analyser/routes/product.py` |
| `GET` | `/api/v1/product/{ean}` | — | `webservices/ratis_product_analyser/routes/product.py` |
| `POST` | `/api/v1/product/{ean}/contribute` | Apply (or queue for admin review) a user contribution on a | `webservices/ratis_product_analyser/routes/product.py` |
| `DELETE` | `/api/v1/product/{ean}/favorite` | — | `webservices/ratis_product_analyser/routes/product.py` |
| `POST` | `/api/v1/product/{ean}/favorite` | — | `webservices/ratis_product_analyser/routes/product.py` |
| `POST` | `/api/v1/scan/barcode` | — | `webservices/ratis_product_analyser/routes/scan.py` |
| `GET` | `/api/v1/scan/check-hash` | Client-side duplicate check — returns {duplicate: bool} without uploading | `webservices/ratis_product_analyser/routes/scan.py` |
| `GET` | `/api/v1/scan/history` | Return the authenticated user's scan history — unified entries | `webservices/ratis_product_analyser/routes/scan.py` |
| `POST` | `/api/v1/scan/label` | — | `webservices/ratis_product_analyser/routes/scan.py` |
| `GET` | `/api/v1/scan/label-group` | Return accepted electronic_label scans for the group (store, date) | `webservices/ratis_product_analyser/routes/scan.py` |
| `POST` | `/api/v1/scan/label/batch` | — | `webservices/ratis_product_analyser/routes/scan.py` |
| `GET` | `/api/v1/scan/label/session/{session_id}` | — | `webservices/ratis_product_analyser/routes/scan.py` |
| `POST` | `/api/v1/scan/receipt` | Upload a receipt photo for OCR processing | `webservices/ratis_product_analyser/routes/scan.py` |
| `GET` | `/api/v1/scan/receipt/{receipt_id}` | — | `webservices/ratis_product_analyser/routes/scan.py` |
| `POST` | `/api/v1/scan/receipt/{receipt_id}/confirm-store` | User confirms the OCR-detected store for a receipt that couldn't be | `webservices/ratis_product_analyser/routes/scan.py` |
| `POST` | `/api/v1/scan/receipt/{receipt_id}/rescan` | Re-trigger the OCR pipeline on an existing receipt | `webservices/ratis_product_analyser/routes/scan.py` |

## ratis_rewards

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/api/v1/admin/achievements` | Return every catalog row + unlock stats | `webservices/ratis_rewards/routes/admin/achievements.py` |
| `POST` | `/api/v1/admin/achievements` | Insert a new achievement + emit ``achievement_created`` audit row | `webservices/ratis_rewards/routes/admin/achievements.py` |
| `DELETE` | `/api/v1/admin/achievements/{achievement_id}` | Hard delete (only allowed if 0 unlocks) | `webservices/ratis_rewards/routes/admin/achievements.py` |
| `PATCH` | `/api/v1/admin/achievements/{achievement_id}` | Partial update with the immutable-after-unlock guard | `webservices/ratis_rewards/routes/admin/achievements.py` |
| `POST` | `/api/v1/admin/users/{user_id}/achievements/{achievement_id}/grant` | Force-unlock an achievement for a user (idempotent) | `webservices/ratis_rewards/routes/admin/achievements.py` |
| `GET` | `/api/v1/admin/battlepass/seasons` | Return every battlepass season ordered by season_number desc | `webservices/ratis_rewards/routes/admin/battlepass.py` |
| `POST` | `/api/v1/admin/battlepass/seasons` | Create a new battlepass season. Operator header required for audit | `webservices/ratis_rewards/routes/admin/battlepass.py` |
| `PATCH` | `/api/v1/admin/battlepass/seasons/{season_id}/activate` | Activate a season — at most one active at a time | `webservices/ratis_rewards/routes/admin/battlepass.py` |
| `POST` | `/api/v1/admin/battlepass/seasons/{season_id}/tiers` | Create a milestone (tier) for a season | `webservices/ratis_rewards/routes/admin/battlepass.py` |
| `POST` | `/api/v1/admin/cab/adjustment` | Manual CAB credit/debit by an admin | `webservices/ratis_rewards/routes/admin/cab.py` |
| `GET` | `/api/v1/admin/cab/users/{user_id}/transactions` | Paginated list of CAB transactions for a user — read-only audit | `webservices/ratis_rewards/routes/admin/cab.py` |
| `GET` | `/api/v1/admin/affiliate-offers` | List all affiliate offers | `webservices/ratis_rewards/routes/admin/cashback.py` |
| `POST` | `/api/v1/admin/affiliate-offers` | Create a new affiliate offer | `webservices/ratis_rewards/routes/admin/cashback.py` |
| `PATCH` | `/api/v1/admin/cashback/{transaction_id}/refuse` | Refuse a cashback CREDIT transaction | `webservices/ratis_rewards/routes/admin/cashback.py` |
| `PATCH` | `/api/v1/admin/cashback/{transaction_id}/validate` | Confirm a cashback CREDIT transaction | `webservices/ratis_rewards/routes/admin/cashback.py` |
| `GET` | `/api/v1/admin/cashback/withdrawals` | Paginated list of cashback withdrawals (read-only audit) | `webservices/ratis_rewards/routes/admin/cashback_withdrawals.py` |
| `PATCH` | `/api/v1/admin/cashback/withdrawals/{withdrawal_id}/refuse` | Refuse a pending withdrawal — log reason, optionally refund balance | `webservices/ratis_rewards/routes/admin/cashback_withdrawals.py` |
| `PATCH` | `/api/v1/admin/cashback/withdrawals/{withdrawal_id}/validate` | Validate a pending withdrawal — initiate payout, record provider ref | `webservices/ratis_rewards/routes/admin/cashback_withdrawals.py` |
| `GET` | `/api/v1/admin/challenges` | List all challenges with computed status, current_count, and milestone_count | `webservices/ratis_rewards/routes/admin/challenges.py` |
| `POST` | `/api/v1/admin/challenges` | Create a new community challenge (inactive by default) | `webservices/ratis_rewards/routes/admin/challenges.py` |
| `PATCH` | `/api/v1/admin/challenges/{challenge_id}/activate` | Set is_active=TRUE. Fails with 409 if another challenge is already active | `webservices/ratis_rewards/routes/admin/challenges.py` |
| `PATCH` | `/api/v1/admin/challenges/{challenge_id}/deactivate` | Set is_active=FALSE | `webservices/ratis_rewards/routes/admin/challenges.py` |
| `POST` | `/api/v1/admin/challenges/{challenge_id}/milestones` | Add a milestone to a challenge | `webservices/ratis_rewards/routes/admin/challenges.py` |
| `GET` | `/api/v1/admin/missions/templates` | Paginated mission catalogue listing | `webservices/ratis_rewards/routes/admin/missions.py` |
| `POST` | `/api/v1/admin/missions/templates` | Create a new mission catalogue row | `webservices/ratis_rewards/routes/admin/missions.py` |
| `PATCH` | `/api/v1/admin/missions/templates/{mission_id}` | Partial update of a mission catalogue row | `webservices/ratis_rewards/routes/admin/missions.py` |
| `GET` | `/api/v1/admin/mystery` | List all mystery challenges, newest first | `webservices/ratis_rewards/routes/admin/mystery.py` |
| `POST` | `/api/v1/admin/mystery` | Create a scheduled mystery challenge | `webservices/ratis_rewards/routes/admin/mystery.py` |
| `GET` | `/api/v1/admin/mystery/draw` | Draw a random eligible product for the next mystery challenge | `webservices/ratis_rewards/routes/admin/mystery.py` |
| `DELETE` | `/api/v1/admin/mystery/{challenge_id}` | Delete a scheduled mystery challenge | `webservices/ratis_rewards/routes/admin/mystery.py` |
| `PATCH` | `/api/v1/admin/mystery/{challenge_id}` | Update a scheduled mystery challenge | `webservices/ratis_rewards/routes/admin/mystery.py` |
| `POST` | `/api/v1/admin/referral/link` | Create a X→Y referral link manually (support-driven datafix) | `webservices/ratis_rewards/routes/admin/referral.py` |
| `GET` | `/api/v1/admin/rewards/configs` | Paginated reward_config listing | `webservices/ratis_rewards/routes/admin/reward_config.py` |
| `POST` | `/api/v1/admin/rewards/configs` | Create a new reward_config row | `webservices/ratis_rewards/routes/admin/reward_config.py` |
| `DELETE` | `/api/v1/admin/rewards/configs/{reward_config_id}` | Hard delete a reward_config row + write a pipeline_audit_log entry | `webservices/ratis_rewards/routes/admin/reward_config.py` |
| `GET` | `/api/v1/admin/rewards/configs/{reward_config_id}` | Fetch a single reward_config by id | `webservices/ratis_rewards/routes/admin/reward_config.py` |
| `PATCH` | `/api/v1/admin/rewards/configs/{reward_config_id}` | Partial update of a reward_config row | `webservices/ratis_rewards/routes/admin/reward_config.py` |
| `POST` | `/api/v1/admin/session-bootstrap` | Mint a 60-second single-use OTT and return the browser-ready URL | `webservices/ratis_rewards/routes/admin/session_bootstrap.py` |
| `GET` | `/api/v1/admin/settings` | Return all settings sections stored in DB | `webservices/ratis_rewards/routes/admin/settings.py` |
| `GET` | `/api/v1/admin/settings/audit` | List audit log entries | `webservices/ratis_rewards/routes/admin/settings.py` |
| `GET` | `/api/v1/admin/settings/audit/{audit_id}` | Return a full audit row including diff (computed on-fly if NULL) | `webservices/ratis_rewards/routes/admin/settings.py` |
| `POST` | `/api/v1/admin/settings/seed` | Re-seed all sections from ratis_settings.json. Safe to call multiple times | `webservices/ratis_rewards/routes/admin/settings.py` |
| `GET` | `/api/v1/admin/settings/{section}` | Return a single settings section | `webservices/ratis_rewards/routes/admin/settings.py` |
| `PUT` | `/api/v1/admin/settings/{section}` | Replace section data (full replace) | `webservices/ratis_rewards/routes/admin/settings.py` |
| `POST` | `/api/v1/admin/settings/{section}/cancel-pending` | Cancel a ``pending_2fa`` audit row — transitions to ``cancelled`` | `webservices/ratis_rewards/routes/admin/settings.py` |
| `POST` | `/api/v1/admin/settings/{section}/confirm-2fa` | Confirm a ``pending_2fa`` audit row via TOTP | `webservices/ratis_rewards/routes/admin/settings.py` |
| `GET` | `/api/v1/admin/settings/{section}/editable` | Return ``{editable: bool, frozen_keys: [...]}`` for a section | `webservices/ratis_rewards/routes/admin/settings.py` |
| `GET` | `/api/v1/admin/stats/cab` | Aggregated CAB-economy stats for the given window | `webservices/ratis_rewards/routes/admin/stats.py` |
| `GET` | `/api/v1/admin/rewards/streak-tiers` | Paginated streak_tiers listing | `webservices/ratis_rewards/routes/admin/streak_tier.py` |
| `POST` | `/api/v1/admin/rewards/streak-tiers` | Create a new streak_tier row | `webservices/ratis_rewards/routes/admin/streak_tier.py` |
| `DELETE` | `/api/v1/admin/rewards/streak-tiers/{streak_tier_id}` | Hard delete a streak_tier row + write a pipeline_audit_log entry | `webservices/ratis_rewards/routes/admin/streak_tier.py` |
| `GET` | `/api/v1/admin/rewards/streak-tiers/{streak_tier_id}` | Fetch a single streak_tier by id | `webservices/ratis_rewards/routes/admin/streak_tier.py` |
| `PATCH` | `/api/v1/admin/rewards/streak-tiers/{streak_tier_id}` | Partial update of a streak_tier row | `webservices/ratis_rewards/routes/admin/streak_tier.py` |
| `GET` | `/api/v1/admin/trust-scores` | Paginated trust-score view for the admin queue | `webservices/ratis_rewards/routes/admin/trust_scores.py` |
| `PATCH` | `/api/v1/admin/users/{user_id}/shadow-ban` | Toggle the ``is_shadow_banned`` flag for a user | `webservices/ratis_rewards/routes/admin/trust_scores.py` |
| `GET` | `/api/v1/gamification/battlepass` | Return the active battlepass season with milestones and computed statuses | `webservices/ratis_rewards/routes/gamification/battlepass.py` |
| `POST` | `/api/v1/gamification/battlepass/claim/{milestone_id}` | Claim a battlepass milestone reward | `webservices/ratis_rewards/routes/gamification/battlepass.py` |
| `GET` | `/api/v1/gamification/challenge` | Return the active (or frozen) community challenge with per-user milestone state | `webservices/ratis_rewards/routes/gamification/challenge.py` |
| `POST` | `/api/v1/gamification/challenge/milestones/{milestone_id}/claim` | Claim a community challenge milestone | `webservices/ratis_rewards/routes/gamification/challenge.py` |
| `GET` | `/api/v1/gamification/leaderboard/burst-alltime` | Return all-time Burst leaderboard + the caller's all-time rank | `webservices/ratis_rewards/routes/gamification/leaderboard.py` |
| `GET` | `/api/v1/gamification/leaderboard/burst-monthly` | Return monthly Burst leaderboard + the caller's rank for the month | `webservices/ratis_rewards/routes/gamification/leaderboard.py` |
| `GET` | `/api/v1/gamification/missions` | Return daily and weekly missions for the authenticated user | `webservices/ratis_rewards/routes/gamification/missions.py` |
| `POST` | `/api/v1/gamification/missions/{user_mission_id}/buffer` | Apply 1 Buffer : target × 2, cab_reward × (n+1), period +1 day | `webservices/ratis_rewards/routes/gamification/missions.py` |
| `POST` | `/api/v1/gamification/missions/{user_mission_id}/burst-claim` | Claim newly-unlocked Burst paliers (XP only, 0 CAB) | `webservices/ratis_rewards/routes/gamification/missions.py` |
| `POST` | `/api/v1/gamification/missions/{user_mission_id}/claim` | Claim a mission — multi-claim cumulatif via double gating | `webservices/ratis_rewards/routes/gamification/missions.py` |
| `POST` | `/api/v1/gamification/missions/{user_mission_id}/freeze` | Freeze a mission — debit CABs, postpone to next period | `webservices/ratis_rewards/routes/gamification/missions.py` |
| `GET` | `/api/v1/gamification/mystery` | Return the active (or frozen) mystery challenge with user-visible clues | `webservices/ratis_rewards/routes/gamification/mystery.py` |
| `GET` | `/api/v1/gamification/mystery/history` | Return the last 10 revealed mystery challenges with product and winner info | `webservices/ratis_rewards/routes/gamification/mystery.py` |
| `GET` | `/api/v1/gamification/mystery/leaderboard` | Return announced finds for the active mystery challenge | `webservices/ratis_rewards/routes/gamification/mystery.py` |
| `GET` | `/api/v1/gamification/streak` | Return the current Feed Jack streak state for the authenticated user | `webservices/ratis_rewards/routes/gamification/streak.py` |
| `POST` | `/api/v1/gamification/streak/feed` | Feed Jack | `webservices/ratis_rewards/routes/gamification/streak.py` |
| `POST` | `/api/v1/gamification/streak/purchase-reserve` | Purchase food reserves for Jack using CABs | `webservices/ratis_rewards/routes/gamification/streak.py` |
| `POST` | `/api/v1/gamification/streak/repair` | Repair a broken streak | `webservices/ratis_rewards/routes/gamification/streak.py` |
| `GET` | `/api/v1/gamification/xp/balance` | Return the authenticated user's XP balance and current level | `webservices/ratis_rewards/routes/gamification/xp.py` |
| `GET` | `/api/v1/rewards/achievements` | Return the full achievement catalog grouped by category, with | `webservices/ratis_rewards/routes/rewards/achievements.py` |
| `POST` | `/api/v1/rewards/achievements/secret-event` | Forward the secret event to the achievement dispatcher | `webservices/ratis_rewards/routes/rewards/achievements.py` |
| `GET` | `/api/v1/rewards/achievements/{achievement_id}` | Single achievement detail. 404 when unknown OR when the user | `webservices/ratis_rewards/routes/rewards/achievements.py` |
| `GET` | `/api/v1/rewards/cab/balance` | Return the authenticated user's CAB balance and battlepass progress | `webservices/ratis_rewards/routes/rewards/cab.py` |
| `GET` | `/api/v1/rewards/cashback/balance` | — | `webservices/ratis_rewards/routes/rewards/cashback.py` |
| `POST` | `/api/v1/rewards/cashback/boost/{transaction_id}` | — | `webservices/ratis_rewards/routes/rewards/cashback.py` |
| `POST` | `/api/v1/rewards/cashback/process-retroactive` | Internal — credit cashback for receipts attached to a store that just | `webservices/ratis_rewards/routes/rewards/cashback.py` |
| `POST` | `/api/v1/rewards/cashback/scan-detected` | Internal endpoint — called by ratis_product_analyser for receipt scans | `webservices/ratis_rewards/routes/rewards/cashback.py` |
| `POST` | `/api/v1/rewards/cashback/webhook/{provider}` | Partner webhook — notifies of cashback validation or refusal | `webservices/ratis_rewards/routes/rewards/cashback_webhook.py` |
| `POST` | `/api/v1/rewards/cashback/withdraw` | — | `webservices/ratis_rewards/routes/rewards/cashback_withdraw.py` |
| `POST` | `/api/v1/rewards/events/action` | Generic gamification event ingestion | `webservices/ratis_rewards/routes/rewards/events.py` |
| `GET` | `/api/v1/rewards/gift-cards` | — | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `POST` | `/api/v1/rewards/gift-cards/annual` | Create a pending gift_card_orders row for an annual subscription | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `GET` | `/api/v1/rewards/gift-cards/cap-usage` | Server-authoritative gift-card cap usage snapshot | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `GET` | `/api/v1/rewards/gift-cards/catalog` | Return active brands + allowed denominations + ratio | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `POST` | `/api/v1/rewards/gift-cards/order` | Spend CAB on a gift card. See ARCH_boutique.md for the full contract | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `GET` | `/api/v1/rewards/gift-cards/{order_id}` | — | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `POST` | `/api/v1/rewards/gift-cards/{order_id}/issue` | Kick off Runa issuance for a pre-existing pending order | `webservices/ratis_rewards/routes/rewards/gift_cards.py` |
| `GET` | `/api/v1/rewards/referral/code` | Return the current user's referral code (lazy-creates on first access) | `webservices/ratis_rewards/routes/rewards/referral.py` |
| `GET` | `/api/v1/rewards/referral/history` | Return the user's referral history (list of filleuls) + aggregated stats | `webservices/ratis_rewards/routes/rewards/referral.py` |
| `POST` | `/api/v1/rewards/referral/signup-bonus` | Award Y's +150 CAB signup bonus. Called by ratis_auth after a successful | `webservices/ratis_rewards/routes/rewards/referral.py` |
| `POST` | `/api/v1/rewards/referral/trigger` | Award CAB + XP + gift card to the referrer upon referred user subscription | `webservices/ratis_rewards/routes/rewards/referral.py` |
| `GET` | `/api/v1/rewards/settings/public` | Return the whitelisted runtime settings | `webservices/ratis_rewards/routes/rewards/settings_public.py` |
| `GET` | `/api/v1/rewards/shop/{brand_id}/usage-stats` | Return the user's aggregate stats for the given gift-card brand | `webservices/ratis_rewards/routes/rewards/shop.py` |

---

**Total endpoints: 203** across 5 services.
