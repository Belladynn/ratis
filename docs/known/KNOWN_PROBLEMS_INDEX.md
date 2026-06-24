# KNOWN_PROBLEMS — Index

Lire cet index avant tout audit. Chercher les mots clés du problème rencontré.
Si trouvé → lire les lignes indiquées dans KNOWN_PROBLEMS.md.
Si nouveau → ajouter en FIN des deux fichiers et incrémenter le numéro.
Si décision actée -> ajouter ligne concernée dans décision_acted.md

**⚠️ Toujours ajouter les nouvelles entrées en fin de fichier — jamais entre deux entrées existantes. Ça évite de rework tout l'index.**

---

## Exemple d'entrée

Le tableau ci-dessous est un EXEMPLE — chaque ligne est indentée par 4 espaces (block code Markdown) pour rester invisible aux greps `^\| KP-NN` qui scannent les vraies entrées.
Toute entrée réelle listée plus bas dans § Index doit suivre ce format mais avec un vrai numéro `KP-NN` (deux chiffres).

    | # | Mots clés | Lignes KNOWN_PROBLEMS.md | Lignes DECISION_ACTED.md |
    |---|---|---|
    | KP-EXAMPLE | mot1, mot2, mot3 | L10-L45 | L-XX - LYY |


---

## Index

| # | Mots clés | Lignes KNOWN_PROBLEMS.md | Lignes DECISION_ACTED.md |
|---|---|---|
| KP-01 | db.commit, mutation silencieuse, rollback, route, persist | L4-L22 |
| KP-02 | fixture, assert_no_pending_changes, savepoint, IntegrityError, test supprimé | L24-L50 |
| KP-03 | centimes, euros, NUMERIC, float, montant, prix, OCR, Decimal | L52-L78 |
| KP-04 | parent_transaction_id, double sémantique, BOOST, WITHDRAWAL, traçabilité | L80-L96 |
| KP-05 | HTTPException, service, Celery, worker, async, exception métier | L98-L118 |
| KP-06 | psycopg2, psycopg3, driver, create_engine, make_engine | L120-L138 |
| KP-07 | rewarded_at, webhook, Stripe, double-reward, parrainage | L140-L158 |
| KP-08 | VALID_REASONS, VALID_REFERENCE_TYPES, frozenset, CHECK constraint, migration, sync, trois sources, cabecoin_transactions, xp_transactions, reference_type, reference_consistency_check, retro_scan drift, set-equality test, pg_get_constraintdef, mission_reward, mission_freeze, gift_card_purchase, buffer, burst, RW-04 | L187-L237 |
| KP-09 | VIEW, ALTER COLUMN, migration, price_history, dépendance, PostgreSQL | L182-L208 |
| KP-10 | ORM model, Base.metadata, create_all, Alembic, NUMERIC, Integer, Decimal, type, test faux positif | L210-L248 |
| KP-11 | flush, commit, fixture, conftest, make_user, assert_no_pending_changes, faux positif, writes | L252-L279 |
| KP-12 | centimes, Decimal, constante test, DB, round, collision, basculement, PRICE_DB, migration | L282-L308 |
| KP-13 | httpx, AsyncClient, timeout, bandit, B113, TCP, blocage, batch | L310-L338 |
| KP-14 | db.commit, GET, route lecture, commit inutile, write silencieux | L325-L348 |
| KP-15 | resolution, validate, service, guard, ValueError, cashback, resolve | L350-L372 |
| KP-16 | ge=0, price, centimes, champ négatif, Pydantic, Field, montant | L374-L396 |
| KP-17 | _mark_failed, WARNING, ERROR, critique, gift_card, stuck, pending | L398-L420 |
| KP-18 | slowapi, rate_limit, 429, TestClient, cross-test, pollution, reset_storage, limiter, conftest | L431-L466 |
| KP-19 | UNIQUE, contrainte, test, batch, données identiques, _JPEG_BYTES, SHA-256, photo_hash, suffixe | L468-L494 |
| KP-20 | PendingRollbackError, IntegrityError, flush raté, DEACTIVE, lazy-load, expire, rollback, except, db.rollback, create_savepoint, savepoint, fixture, FK violation | L498-L541 |
| KP-21 | db_transaction, rollback, pre-write, exception, BelowMinimum, setup, flush, commit, helper, test, SAVEPOINT, begin_nested, assert_no_pending_changes | L543-L591 |
| KP-22 | import circulaire, lazy import, repository, dépendance, orchestration, reward, award_cab, award_xp, challenge | L593-L626 |
| KP-23 | index partiel, partial index, __table_args__, create_all, Alembic, migration, modèle, contrainte, unique, IntegrityError, TOCTOU, test, community_challenges_one_active | L628-L672 |
| KP-24 | R2, boto3, S3, service externe, boucle, commit, crash safety, dry-run, early return, BotoCoreError, ClientError, exception, socket, urllib3, image, purge, delete | L674-L715 |
| KP-25 | .env.prod, CRLF, Windows, line endings, \r, JWT_SECRET, R2_ACCESS_KEY_ID, length, InvalidArgument, env_file, force-recreate, restart | L709-L734 |
| KP-26 | expo-auth-session, providers/google, deep-link, oauthredirect, unmatched route, custom-tab, @react-native-google-signin, GoogleSignin.configure, post-veille, "Erreur inattendue" | L735-L762 |
| KP-27 | eas update, --environment, --channel, .env.local, EXPO_PUBLIC_, LAN URL, fallback hardcodé, requireEnv, env:create, env:list, OTA bundle | L763-L786 |
| KP-28 | EXIF, orientation, portrait, capteur, expo-image-manipulator, skipProcessing, takePictureAsync, PaddleOCR, OCR à l'envers, manipulateAsync, ImageOps.exif_transpose, resize, downscale | L787-L813 |
| KP-29 | .env.local, load_dotenv, setdefault, JWT_SECRET, conftest, pytest, 401, Unauthorized, placeholder, hermétique, force-set, sentinelle, create_all, schema, pollution | L817-L857 |
| KP-30 | worktree, isolation, Edit tool, path absolu, main checkout, agent-id, orphan files, SA dispatch, claude code, cwd, harness | L862-L900 |
| KP-31 | Anthropic, prompt caching, cache_control, ephemeral, 4096 tokens, Haiku, threshold, minimum prefix, cache_read_input_tokens, no-op silencieux, few-shot, system prompt | L905-L945 |
| KP-32 | OTA channel, eas update, eas build, preview, production, mismatch, silent no-op, alpha APK, side-load, badge OTA, force-stop ×2, R34, ota-push.sh | L938-L966 |
| KP-33 | alembic_version, divergence, manual SQL, drift, prod, indexes, DROP INDEX, migration de réconciliation, idempotente, partial_indexes_policy, option B, recreate, history rewriting | L968-L1001 |
| KP-34 | OTA, native module, ExpoImageManipulator, Cannot find native module, ErrorBoundary undefined, expo-image-manipulator, package.json deps, eas update:roll-back-to-embedded, rebuild EAS, AppCrashScreen, AF-15 | L1003-L1046 |
| KP-35 | SA dispatch parallèle, worktree, isolation, race, files clash, git checkout overwrite, .claude/worktrees, agent-id, R30, claude code harness | L1048-L1077 |
| KP-36 | self-hosted runners, DinD, Docker-in-Docker, runner_default, ratis_ratis_net, network not found, ephemeral network, services block, DOCKER_HOST tcp dind 2375, ratis_migrations workflow | L1079-L1135 |
| KP-37 | re.IGNORECASE, char class, accents, é vs É, OCR FR, store_detector, phone, regex Python, NFD normalize, fuzzy match | L1137-L1161 |
| KP-38 | worktree, ratis_client, Expo, npm install, node_modules, gitignored, jest, frontend, SA dispatch, performance setup, FE-only | L1162-L1185 |
| KP-39 | Celery worker, sys.path, ModuleNotFoundError, storage, import relatif, PR #152, FastAPI vs Celery, worker boot, lazy import, ratis_product_analyser | L1186-L1218 |
| KP-40 | pytest, Windows, bash MINGW, stdout buffer, hang, output empty, taskkill, exit code 0, R15, CI Linux Docker ground truth, PYTHONUNBUFFERED, --capture=no, -s, PowerShell | L1219-L1244 |
| KP-41 | handle_barcode_rescan, race condition, IntegrityError, receipts.receipt_barcode unique, concurrent scans, advisory lock, pg_advisory_xact_lock, scan_repository, alpha, mini-PR | L1245-L1284 |
| KP-42 | migration Alembic, backfill, stores.validation_status, user_suggested, admin manual validate, cashback gating, ARCH_store_validation Pitfall P-2, PR-B, prod safety, idempotent | L1285-L1315 |
| KP-43 | pytest-timeout, pyproject, dependency-groups, uv workspace, uv sync --group dev, timeout silently ignored, hang CI, self-hosted runner, PR #233, PR #234, sanity probe | L1317-L1336 |
| KP-44 | TIMESTAMP, TIMESTAMPTZ, timezone-aware, timezone-naive, Mapped[datetime], create_all, drift modèle migration, admin_audit, expires_at, applied_at, aware/naive comparison, Bloc B admin, PR #258 | L1340-L1375 |
| KP-45 | alembic, multi heads, merge revision, parallel PRs, down_revision, DAG, upgrade head, CI fail, PR #259, PR #261, PR #263 | L1377-L1399 |
| KP-46 | alembic, ambiguous walk, downgrade -1, merge revision, multiple parents, test_migration.sh, CI workflow, ratis_migrations, PR #263 | L1401-L1420 |
| KP-47 | assert_no_pending_changes, db.add, db.flush, db.commit, fixture, faux positif, _writes, INSERT direct, seeds, Bloc B admin, PR #258 | L1422-L1450 |
| KP-48 | HTTPException, detail, dict, nesting, FastAPI wrap, body, i18n, frozen_key_modified, R12, PR #258, PR #267 | L1452-L1483 |
| KP-49 | PostgreSQL, ENUM type, native, postgresql.ENUM, DROP TYPE IF EXISTS, downgrade, idempotent, admin_settings_audit_status, TEXT + CHECK, convention repo, PR #257 | L1485-L1507 |
| KP-50 | users_provider_check, CHECK constraint, admin seed, pg_dump, drift sémantique, provider, RGPD, admin_users, service accounts, PR #257 | L1509-L1532 |
| KP-51 | FastAPI, route ordering, literal vs path-param, /audit, /{section}, get_section, list_audit, 404 settings_section_not_found, PR #269 | L1534-L1568 |
| KP-52 | CI pytest, deadlock, DROP SCHEMA public CASCADE, setup_db fixture, flake, gh run rerun, jobs concurrents, ratis_test partagé, V2 isolation per-job, PR #269, PR #270 | L1570-L1589 |
| KP-53 | healthchecks self-hosted, Docker healthcheck, curl missing, wget missing, image minimale, Python urllib, infra/itops, Phase A | L1591-L1618 |
| KP-54 | healthchecks self-hosted, API v1 vs v3, /accounts/login, auth required, Django, healthcheck Docker, Phase A, infra/itops | L1620-L1639 |
| KP-55 | healthchecks self-hosted, EMAIL_PORT, envint, ValueError, empty string, env var int, Django settings, .env.example, Phase A | L1641-L1666 |
| KP-56 | Watchtower, Docker API version, 1.25 vs 1.40, DOCKER_API_VERSION, client unsupported, containrrr/watchtower, infra/itops, Phase A | L1668-L1695 |
| KP-57 | eas update, --environment, --channel, EXPO_PUBLIC_API_URL, env var missing at runtime, OTA crash, post-Mac-mini migration, .env.local non restauré, Sentry RATIS-CLIENT-N, R34, EAS Update v17+ | L1697-L1731 |
| KP-58 | security CLI, macOS-only, Keychain.get, FileNotFoundError, subprocess.run, Linux CI, agent-mcp, fake_runner, runner kwarg injection, monkeypatch Keychain __init__ | L1733-L1769 |
| KP-59 | n8n, Code node, require, crypto, sandbox, NODE_FUNCTION_ALLOW_BUILTIN, builtin module, HMAC, Cannot find module | L1781-L1801 |
| KP-60 | n8n, webhook, webhookId, hand-crafted JSON, import:workflow, unknown webhook, 404 not registered, route registration | L1803-L1827 |
| KP-61 | n8n, Merge node, combineByPosition, alwaysOutputData, empty response, 0 items, workflow stops silently, graceful degradation, enrichment | L1829-L1851 |
| KP-62 | n8n, webhook, rawBody, item.binary.data, HMAC verification, body re-stringification, JSON.stringify mismatch, raw bytes, base64, openssl dgst, --data-binary | L1853-L1893 |
| KP-63 | n8n, credentials, import:workflow, credential link, by name, UUID, hand-crafted JSON, ratis-notion-incidents, ratis-github | L1895-L1923 |
| KP-64 | UNIQUE, NULLS NOT DISTINCT, nullable, qualifier, Postgres, PG 15+, IntegrityError, DID NOT RAISE, silent duplicate, extend constraint, migration UPDATE, postgresql_nulls_not_distinct, op.execute ALTER, missions_catalog_v1, PR #324 | L1927-L1967 |
| KP-65 | extract_store_signals, store_detector, _city_raw, _raw_barcode, signals filter, prefix underscore, store_candidates, city perdue, OCR pipeline, V2 refinement, audit silent drops | L1971-L1981 |
| KP-66 | pyzbar, receipt_barcode, OCR barcode fallback, _raw_barcode, store_detector, barcode_reader, silent drop, NULL barcode, audit OCR | L1985-L1995 |
| KP-67 | address regex, 18 TER, 42 BIS, QUATER, store_detector, _ADDRESS_KEYWORDS_BY_COUNTRY, _get_address_re, address_guess vide, store_status unknown, audit OCR | L1999-L2009 |
| KP-68 | v2_assembly, _v2_output_to_receipt_data, find_price_for_cluster, no nearby price, continue silent, pending_items, rejected_reason, contrat pipeline, audit OCR | L2013-L2023 |
| KP-69 | y_tolerance, hardcoded 30, _v2_output_to_receipt_data, find_price_for_cluster, hauteur médiane, cluster height, scaling pipeline, audit OCR | L2027-L2037 |
| KP-70 | finalize_receipt, total_amount=None, Branch A, store_id is None, pending receipt, receipt_data total, audit OCR, silent drop total | L2041-L2051 |
| KP-71 | total_amount, MONTANT DU, LLM classify total, parse_receipt fallback, receipt_data is None, _TOTAL_RE, legacy parser, par-champ fallback, audit OCR | L2055-L2065 |
| KP-72 | process_pending_items, total_amount NULL, Branch A, store confirm, pending → scans, receipt aggregate, backfill, scan_repository, audit OCR | L2069-L2079 |
| KP-73 | rejected_reason, no_usable_receipt_data, blurry, arbitrate, OCR pipeline fail, _run_ocr_pipeline, assess_quality, propagation cause, diagnostic terrain, audit OCR | L2083-L2093 |
| KP-74 | header_lines, lines[:8], truncation, store_detector, retailer perdu, postal_code perdu, header window, OCR header, audit OCR | L2097-L2107 |
| KP-75 | achievement, unique_products_discovered_count, exp_unknown_10, Pionnier, first_discovered_by_user_id, achievements V1.1, placeholder False, RÉSOLU 2026-05-10 | L2111-L2123 |
| KP-76 | achievement, progress, serializer, _compute_progress, AchievementCard, X/Y bar, achievements V1.1, RÉSOLU 2026-05-10 | L2127-L2137 |
| KP-77 | notifier, quiet_hours, off-by-one, boundary, 22h-8h, Paris, push notification rate limit, test skip, DP-quiet-hours-paris-boundary | L2141-L2151 |
| KP-78 | Pattern A, DEFERRED_PG_ONLY_CONSTRAINTS, provider_check, auth_coherence, create_all, CheckConstraint, ORM drift, schema sync, DELETE /account, RGPD anonymize, tombstone, OAuth merge, link-by-email, password_hash NULL, silent IntegrityError, Bug 5, RÉSOLU 2026-05-11 | L2157-L2181 |
| KP-79 | require_env, docker-compose.prod.yml, environment passthrough, RGPD_ANONYMIZE_SALT, REDIS_URL, auth boot, notifier boot, RuntimeError missing env, fail-fast lifespan, R20, deploy crash, Bug 5 collateral, PR #399, test_compose_env_passthrough.py, AST parsing, security.yml CI guard, drift detection, RÉSOLU 2026-05-12 | L2186-L2218 |
| KP-80 | GIN trgm, pg_trgm, OR clause, function-wrapped, immutable_unaccent, BitmapOr, seq scan, product search, name_normalized, brands_text, alembic migration, functional index, performance, slow query, 2-3s latency, AddBar dropdown, R8 psycopg-v3, wave 6, PR #431, RÉSOLU 2026-05-13 | L2226-L2274 |
| KP-81 | migrate-prod.sh, alembic upgrade head, docker compose run --rm, stale image, migrations profile, git pull, silent success, version_num, 20260513_1000_btxttrgm, wave 6, deploy-prod.sh, ops_lib.sh, R29, scripts/migrate-prod.sh, image rebuild, RÉSOLU 2026-05-14 | L2276-L2318 |
| KP-82 | onPressIn, onPress, Pressable, touch-up, touch-down, dropdown, autocomplete, TextInput, onBlur, blur cascade, focus, conditional render, React Native, racing event, hitSlop, AddBar, wave 5, PR #430, jest fireEvent.press, fireEvent.pressIn, gesture handler, RÉSOLU 2026-05-12 | L2320-L2365 |
| KP-83 | celery, list_optimiser_worker, sys.path, PYTHONPATH, ModuleNotFoundError, services, route_service, docker-compose.prod.yml, worker boot, FastAPI vs Celery script, dev/prod divergence, KP-39 cousin, PR #436, RÉSOLU 2026-05-13 | L2369-L2403 |
| KP-84 | Pydantic, extra=forbid, extra=allow, search_term, query, AddItemRequest, list items, POST /lists/{id}/items, FE contract drift, silent 200, no INSERT, add_item_to_list, return None, ItemResolutionError, list-client.ts, waves 5-9, R8 schema contract, RÉSOLU 2026-05-13 | L2405-L2445 |
| KP-85 | Sentry, auth token, project:releases, org:read, event:read, sourcemap upload, gradle plugin, EAS build, 401, 403, SENTRY_DISABLE_AUTO_UPLOAD, symbolicate, stack trace, JS bundle, agent-mcp keychain, sentry-tools, RÉSOLU 2026-05-14 | L2447-L2495 |
| KP-86 | expo-build-properties, app.config.ts, ConfigContext, ExpoConfig, GOOGLE_MAPS_API_KEY, googleMapsApiKey, Info.plist, GMSApiKey, AndroidManifest, com.google.android.geo.API_KEY, build properties vs app config, env injection, per-platform keys, react-native-maps, PROVIDER_GOOGLE, PR #444, PR #446, RÉSOLU 2026-05-14, OBSOLETE 2026-05-25, DA-46, app.config.ts supprimé, MapTiler, EXPO_PUBLIC_MAPTILER_KEY | L2503-L2567 |
| KP-87 | libmagic, file-5.41, python-magic, magic.from_buffer, image/webp, RIFF, WEBP, VP8, FourCC, signature fallback, _looks_like_webp, validate_image_upload, uploads.py, 422 unsupported_file_type, application/octet-stream, Pillow, test_webp_accepted, Bug 8, PR #448, RÉSOLU 2026-05-14 | L2565-L2587 |
| KP-88 | assert_no_pending_changes, client fixture, db.commit, db.flush, TestClient, seeding, route test, endpoint test, conftest, autouse fixture, request.fixturenames, double-session, KP-11 cousin, KP-47 cousin, PR #449, SA_DEV.md to-update | L2589-L2617 |
| KP-89 | products.source, source_check, CHECK constraint, user_suggested, filtre défensif, incomplete_service, product_search, IntegrityError, test non-couvrable, future-proof, schema ORM drift, Pattern A, PR #453, OUVERT | L2619-L2639 |
| KP-90 | ruff, F821, undefined name, forward-ref, string annotation, function-scoped import, TYPE_CHECKING, from __future__ import annotations, _make_user, PR #453, RÉSOLU 2026-05-14 | L2641-L2664 |
| KP-91 | gh pr merge, --delete-branch, worktree, git checkout, branche occupée, isolation worktree, SA parallèle, KP-30 cousin, KP-35 cousin, PR #455, OUVERT | L2666-L2686 |
| KP-92 | react-native-maps, maplibre, @maplibre/maplibre-react-native, eas update, --platform, web bundling, codegenNativeCommands, native-only module, MapMarkerNativeComponent, route-map.tsx, --platform=all, platforms ios android, OTA wave-13, PR #444, RÉSOLU 2026-05-15, DA-46, revert, lib carto native-only générique | L2696-L2727 |
| KP-93 | subagent, worktree, isolation worktree, cwd, git branch --show-current, commit mauvaise branche, worktree-agent, dangling stash, git worktree remove -f -f, brief SA, KP-91 cousin, KP-30 cousin, KP-35 cousin, OUVERT | L2721-L2739 |
| KP-94 | purchased_not_future, fixture, date absolue, date.today, CURRENT_DATE, flake, passage de minuit, horloge host vs serveur PG, dates relatives, audit 2026-05-17, OUVERT | L2743-L2759 |
| KP-95 | parrainage, signup, referral_code, register supprimé, OAuth-only, filleul, redemption code, deep-link, DA-39, Phase 1, OUVERT | L2759-L2776 |
| KP-96 | n8n, n8n execute, CLI, Schedule Trigger, cron, Missing node to start execution, Execute Workflow Trigger, smoke test, Test workflow, daily-digest, OUVERT | L2780-L2792 |
| KP-97 | docker-compose, environment, env_file, whitelist, .env, substitution ${VAR}, var absente du container, force-recreate, n8n, daily-digest, OUVERT | L2796-L2808 |
| KP-98 | n8n, CLI, user-management:reset, update:workflow, restart container, isInstanceOwnerSetUp, signin, état en mémoire, activation workflow, OUVERT | L2812-L2824 |
| KP-99 | alembic, multiple head revisions, double-head, dual head, down_revision, merge migration, alembic merge heads, parallel PR, stale PR merge, branch protection, free GitHub plan, alembic_heads.yml, alembic_autoheal.yml, auto-heal, self-hosted runner, GITHUB_TOKEN, PR #510, PR #513, RÉSOLU 2026-05-18 | L2828-L2850 |
| KP-100 | TOCTOU, race condition, check-then-act, link_provider, unlink_provider, user_identities, UNIQUE provider provider_id, IntegrityError, LinkConflictError, identity_already_linked, cannot_unlink_last_identity, advisory lock, pg_advisory_xact_lock, account/identities, link-provider, DA-45, OAuth Phase 2, KP-41 cousin, OUVERT | L2852-L2872 |
| KP-101 | jest, flake, waitFor, asyncUtilTimeout, testTimeout, findBy, testing-library, self-hosted runner, Mac mini charge, faux rouge, useProductByEan, PR #594, RÉSOLU 2026-06-06 | L2876-L2890 |
| KP-102 | Hermès, Codex, openai-codex, ChatGPT Plus, OAuth, 429, usage_limit_reached, token_revoked, exhausted, timeout, fallback_providers, auto-codex-reset, postmortem, agentic, OUVERT (mitigé) | L2892-L2906 |
| KP-103 | worktree, skills, .claude/skills, stale branch, reload-skills, behind main, versionné, repo-worktree-asset-sync-check, OUVERT | L2908-L2922 |
| KP-104 | langfuse, @observe, observe decorator, auto-init, OpenTelemetry, OTEL, span processor, exporter, Failed to export span batch, LANGFUSE_TRACING_ENABLED, kill-switch, no-op, clés vides, init_langfuse, AnthropicInstrumentor, DA-LO6, Celery worker prefork, PR #607, RÉSOLU 2026-06-19 | L2928-L2940 |
| KP-105 | ruff, UP045, UP007, Optional, X \| None, PEP 604, autofix, SQLAlchemy 2.0, Mapped, MappedAnnotationError, forward-ref, string annotation, partially-quoted union, mapper configuration, per-file-ignores, models, quality gates, OUVERT | L2944-L2956 |
