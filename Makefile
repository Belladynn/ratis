.PHONY: help seed-db-init seed-rebuild seed-wipe seed-barcodes dev-up seed-up

# Compose project name — pinned to "ratis" so commands work identically from
# the main checkout AND from any .worktrees/<name>/ subdir (docker compose
# normally derives the project name from the directory, which differs between
# worktrees and would target a *different* postgres container).
COMPOSE_PROJECT ?= ratis

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

seed-db-init: ## Bootstrap ratis_seed DB (DROP + CREATE + alembic upgrade head). Idempotent.
	@echo "[seed-db-init] dropping + recreating ratis_seed..."
	docker compose -p $(COMPOSE_PROJECT) exec -T postgres \
		psql -U ratis -d postgres -v ON_ERROR_STOP=1 \
		-c "DROP DATABASE IF EXISTS ratis_seed;" \
		-c "CREATE DATABASE ratis_seed OWNER ratis;"
	@echo "[seed-db-init] running alembic upgrade head on ratis_seed..."
	DATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed uv run alembic upgrade head  # pragma: allowlist secret
	@echo "[seed-db-init] done — ratis_seed schema at head."

seed-rebuild: seed-db-init ## Re-init ratis_seed + run scripts/seed/main.py (full pipeline).
	@echo "[seed-rebuild] running scripts/seed/main.py against ratis_seed…"
	@# Dev-local PG creds (Docker compose) — not a real secret.
	DATABASE_URL='postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed' ENVIRONMENT=seed uv run --group seed python -m scripts.seed.main  # pragma: allowlist secret
	@echo "[seed-rebuild] done."

seed-wipe: ## TRUNCATE seeded tables on ratis_seed + re-run scripts/seed/main.py (no DROP/alembic).
	@echo "[seed-wipe] truncating seeded tables (CASCADE) on ratis_seed…"
	@# Dev-local PG creds (Docker compose) — not a real secret. DA-5 guards
	@# in wipe.py refuse to run if ENVIRONMENT=production or URL lacks _seed/_dev.
	DATABASE_URL='postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed' ENVIRONMENT=seed uv run --group seed python -m scripts.seed.wipe  # pragma: allowlist secret
	@echo "[seed-wipe] re-running scripts/seed/main.py…"
	DATABASE_URL='postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed' ENVIRONMENT=seed uv run --group seed python -m scripts.seed.main  # pragma: allowlist secret
	@echo "[seed-wipe] done."

seed-barcodes: ## Generate docs/seed/barcodes.html (Step 2-bis).
	@echo "[seed-barcodes] regenerating docs/seed/barcodes.html…"
	uv run --group seed python -m scripts.seed.barcodes
	@echo "[seed-barcodes] done."

dev-up: ## Switch to dev mode (ratis_dev DB) + start local stack
	@# dev/seed templates consolidated into .env.example. Write a dev .env.local.
	printf 'ENVIRONMENT=dev\nDATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_dev\nREDIS_URL=redis://localhost:6379/0\n' > .env.local  # pragma: allowlist secret
	docker compose -p $(COMPOSE_PROJECT) up -d

seed-up: ## Switch to seed mode (ratis_seed DB) + start local stack
	@# dev/seed templates consolidated into .env.example. Write a seed .env.local.
	printf 'ENVIRONMENT=seed\nDATABASE_URL=postgresql+psycopg://ratis:ratis@localhost:5432/ratis_seed\nREDIS_URL=redis://localhost:6379/0\n' > .env.local  # pragma: allowlist secret
	docker compose -p $(COMPOSE_PROJECT) up -d
