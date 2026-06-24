"""Set required env vars before tools.sentry_webhook is imported."""

import os

os.environ.setdefault("SENTRY_WEBHOOK_SECRET", "test-secret-for-tests")
os.environ.setdefault("NOTION_TOKEN", "secret_test_token_for_tests")
os.environ.setdefault("NOTION_DATABASE_ID", "ba6d932f-5647-4035-9b73-b477f6d87181")
