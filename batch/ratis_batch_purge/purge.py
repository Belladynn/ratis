"""
Purge batch — run daily via cron / GitHub Actions scheduled workflow.

Operations (in order, each in its own transaction):
  1. refresh_tokens              — delete expired or revoked rows (revoked kept 90 days for audit)
  2. optimized_routes            — delete expired routes (TTL 24h)
  3. notification_logs           — delete rows older than 90 days
  4. user_sessions               — aggregate into user_session_stats then delete rows older than 90 days
  5. photo_hashes                — release stuck photo_hash locks older than 1h
  6. receipt_images              — delete R2 ticket images older than 48h; mark image_deleted_at
  7. label_images                — delete R2 label images after their 72h retention window; clear label_r2_key
  8. expire_community_challenges — deactivate challenges whose grace period has elapsed
  9. label_pending_orphans       — reject label scans stuck in pending for more than 2h
 10. unknown_scans                — aggregate unknown-store scans older than 7d (weekly bucket), then hard-delete
 11. scan_debug                   — delete scan_debug rows + their R2 processed-image objects after 48h TTL (PR #126)

Usage:
  uv run python batch/ratis_batch_purge/purge.py            # normal run
  uv run python batch/ratis_batch_purge/purge.py --dry-run  # log counts, no commit
"""

import argparse
import logging
import os
import sys

import boto3
from ratis_core.database import make_engine
from ratis_core.observability import init_sentry
from ratis_core.startup import require_env
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("purge")

BATCH_NAME = "purge"


def _get_r2_client():
    """Build a boto3 S3 client pointing at Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def _r2_delete(key: str, client) -> bool:
    """Delete one object from R2. Returns True on success, logs + returns False on error."""
    bucket = os.environ["R2_BUCKET_NAME"]
    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        log.warning("R2 delete failed for %s: %s", key, exc)
        return False


def _run(db, label: str, sql: str, params: dict | None = None) -> int:
    """Execute a DML statement, log the row count, return it."""
    result = db.execute(text(sql), params or {})
    count = result.rowcount
    log.info("%s: %d row(s) affected", label, count)
    return count


def purge_refresh_tokens(Session, dry_run: bool) -> None:
    with Session() as db:
        _run(
            db,
            "refresh_tokens",
            "DELETE FROM refresh_tokens "
            "WHERE expires_at < now() "
            "OR (revoked_at IS NOT NULL AND revoked_at < now() - interval '90 days')",
        )
        if not dry_run:
            db.commit()


def purge_optimized_routes(Session, dry_run: bool) -> None:
    with Session() as db:
        _run(
            db,
            "optimized_routes",
            "DELETE FROM optimized_routes WHERE expires_at < now()",
        )
        if not dry_run:
            db.commit()


def purge_notification_logs(Session, dry_run: bool) -> None:
    with Session() as db:
        _run(
            db,
            "notification_logs",
            "DELETE FROM notification_logs WHERE sent_at < now() - interval '90 days'",
        )
        if not dry_run:
            db.commit()


def aggregate_and_purge_sessions(Session, dry_run: bool) -> None:
    """
    Aggregate user_sessions older than 90 days into user_session_stats,
    then delete the source rows — both in a single atomic transaction.

    now() is captured once at the start of the transaction so that the upsert
    and the delete operate on exactly the same set of rows.
    """
    with Session() as db:
        cutoff = db.execute(text("SELECT now() - interval '90 days'")).scalar()

        result = db.execute(
            text("""
                INSERT INTO user_session_stats
                    (user_id, period_year, period_month, ios_count, android_count, web_count)
                SELECT
                    user_id,
                    EXTRACT(YEAR  FROM started_at)::int,
                    EXTRACT(MONTH FROM started_at)::int,
                    COUNT(*) FILTER (WHERE platform = 'ios'),
                    COUNT(*) FILTER (WHERE platform = 'android'),
                    COUNT(*) FILTER (WHERE platform = 'web')
                FROM user_sessions
                WHERE started_at < :cutoff
                GROUP BY
                    user_id,
                    EXTRACT(YEAR  FROM started_at)::int,
                    EXTRACT(MONTH FROM started_at)::int
                ON CONFLICT (user_id, period_year, period_month) DO UPDATE SET
                    ios_count     = user_session_stats.ios_count     + EXCLUDED.ios_count,
                    android_count = user_session_stats.android_count + EXCLUDED.android_count,
                    web_count     = user_session_stats.web_count     + EXCLUDED.web_count
            """),
            {"cutoff": cutoff},
        )
        log.info("user_session_stats: %d row(s) upserted", result.rowcount)

        _run(
            db,
            "user_sessions purge",
            "DELETE FROM user_sessions WHERE started_at < :cutoff",
            {"cutoff": cutoff},
        )
        if not dry_run:
            db.commit()


def _write_sync_log(Session, status: str, dry_run: bool) -> None:
    if dry_run:
        return
    with Session() as db:
        db.execute(
            text("INSERT INTO batch_sync_log (batch_name, status) VALUES (:name, :status)"),
            {"name": BATCH_NAME, "status": status},
        )
        db.commit()


def purge_photo_hashes(Session, dry_run: bool) -> None:
    """
    Release photo_hash locks blocked in pending state for more than 1 hour.

    Two cases:
    - receipts: photo_hash IS NOT NULL, created_at > 1h ago, no scan has reached a
      terminal state yet — this includes receipts with zero scans (OCR worker
      crashed before the scan row was created) as well as receipts whose scan
      is still pending. Releasing the hash lets the user legitimately retry.
    - label scans: photo_hash IS NOT NULL, scan_type = 'electronic_label',
      status = 'pending', scanned_at > 1h ago (label OCR worker crashed).

    Freeing the hash allows users to legitimately retry submission.
    Hashes on successfully processed receipts/scans are NOT cleared here —
    those are intentionally kept for permanent deduplication.
    """
    with Session() as db:
        _run(
            db,
            "photo_hashes_receipts",
            """
            UPDATE receipts
            SET photo_hash = NULL
            WHERE photo_hash IS NOT NULL
              AND created_at < now() - interval '1 hour'
              AND NOT EXISTS (
                SELECT 1 FROM scans
                WHERE scans.receipt_id = receipts.id
                  AND scans.status NOT IN ('pending')
              )
            """,
        )
        _run(
            db,
            "photo_hashes_label_scans",
            """
            UPDATE scans
            SET photo_hash = NULL
            WHERE photo_hash IS NOT NULL
              AND scan_type = 'electronic_label'
              AND status = 'pending'
              AND scanned_at < now() - interval '1 hour'
            """,
        )
        if not dry_run:
            db.commit()


def purge_receipt_images(Session, dry_run: bool) -> None:
    """
    Delete ticket images from R2 when they are older than 48 hours.

    RGPD: ticket images are PII-adjacent (store name + prices visible).
    48h is enough for OCR to complete; after that they must be purged.
    """
    with Session() as db:
        rows = db.execute(
            text("""
            SELECT id, image_r2_key FROM receipts
            WHERE image_r2_key IS NOT NULL
              AND image_uploaded_at < now() - interval '48 hours'
              AND image_deleted_at IS NULL
        """)
        ).fetchall()

        log.info("receipt_images: %d eligible for deletion", len(rows))

        if not dry_run:
            client = _get_r2_client()
            r2_failures = 0
            for row in rows:
                if _r2_delete(row.image_r2_key, client):
                    db.execute(
                        text("UPDATE receipts SET image_deleted_at = now() WHERE id = :id"),
                        {"id": str(row.id)},
                    )
                    db.commit()
                else:
                    r2_failures += 1

            if r2_failures:
                # RGPD: an undeleted receipt image keeps PII (store name +
                # prices) on R2 past the 48h retention window. A swallowed
                # warning leaves this invisible — raise so the run is marked
                # 'failed' and an explicit error reaches Sentry.
                msg = f"receipt image R2 deletion failed for {r2_failures} object(s) — RGPD 48h retention breached"
                log.error(msg)
                raise RuntimeError(msg)


def purge_label_images(Session, dry_run: bool) -> None:
    """
    Delete label (electronic price tag) images from R2 after their 72h retention window.

    Label images are NOT PII but are stored temporarily for fine-tuning.
    Once label_image_expires_at passes, they can be released.
    """
    with Session() as db:
        rows = db.execute(
            text("""
            SELECT id, label_r2_key FROM scans
            WHERE label_r2_key IS NOT NULL
              AND label_image_expires_at IS NOT NULL
              AND label_image_expires_at < now()
        """)
        ).fetchall()

        log.info("label_images: %d eligible for deletion", len(rows))

        if not dry_run:
            client = _get_r2_client()
            r2_failures = 0
            for row in rows:
                if _r2_delete(row.label_r2_key, client):
                    db.execute(
                        text("UPDATE scans SET label_r2_key = NULL WHERE id = :id"),
                        {"id": str(row.id)},
                    )
                    db.commit()
                else:
                    r2_failures += 1

            if r2_failures:
                # A label image left on R2 past its retention window must not
                # pass silently — raise so the run is marked 'failed' and the
                # error is captured by Sentry.
                msg = f"label image R2 deletion failed for {r2_failures} object(s) — retention window breached"
                log.error(msg)
                raise RuntimeError(msg)


def expire_community_challenges(Session, dry_run: bool) -> None:
    """
    Mark community challenges as inactive once their grace period has elapsed.

    ends_at + grace_period_days is the true deadline — challenges remain visible
    (is_active=TRUE) during the grace window to let users claim final rewards.
    """
    with Session() as db:
        _run(
            db,
            "expire_community_challenges",
            """
            UPDATE community_challenges
            SET is_active = FALSE
            WHERE is_active = TRUE
              AND ends_at + grace_period_days * interval '1 day' < now()
            """,
        )
        if not dry_run:
            db.commit()


def purge_label_pending_orphans(Session, dry_run: bool) -> None:
    """
    Reject label scans stuck in 'pending' for more than 2 hours.

    These are scans whose OCR job was never picked up (worker crash, queue failure).
    Rejecting them unblocks the user from resubmitting.
    Note: photo_hash cleanup for stuck label scans is handled by purge_photo_hashes.
    """
    with Session() as db:
        _run(
            db,
            "label_pending_orphans",
            """
            UPDATE scans
            SET status = 'rejected',
                rejected_reason = 'ocr_timeout',
                status_updated_at = now()
            WHERE scan_type = 'electronic_label'
              AND status = 'pending'
              AND scanned_at < now() - interval '2 hours'
            """,
        )
        if not dry_run:
            db.commit()


def purge_scan_debug(Session, dry_run: bool) -> None:
    """
    Delete expired scan_debug rows + their R2 processed-image objects.

    PR #126 — alpha debug instrumentation. Each row has a 48h TTL
    (purge_after column, indexed). Rows are written by process_receipt
    only when STORE_DEBUG=true ; outside debug windows the table is
    empty and this step is a fast no-op.

    PR #132 — extended visibility : iterate over ``processed_images_r2_keys``
    (JSONB map, up to 4 keys per row : corrected/clahe/binarized/inverted)
    rather than a single ``processed_image_r2_key``. Falls back to the
    legacy column for rows written before PR #132.
    """
    with Session() as db:
        rows = db.execute(
            text("""
            SELECT id, processed_image_r2_key, processed_images_r2_keys
            FROM scan_debug
            WHERE purge_after < now()
        """)
        ).fetchall()

        log.info("scan_debug: %d row(s) eligible for purge", len(rows))

        if not dry_run and rows:
            client = _get_r2_client()
            for row in rows:
                # New JSONB column — up to 4 R2 keys per row. Each value
                # may be None when that pass image upload failed (still a
                # key, but nothing to delete).
                keys_map = row.processed_images_r2_keys
                if keys_map and isinstance(keys_map, dict):
                    for key in keys_map.values():
                        if key:
                            _r2_delete(key, client)
                # Legacy single-key column — covers rows written before
                # PR #132 OR the back-compat copy for new rows. Only
                # delete it if it isn't already in the JSONB map (avoids
                # double-deleting / spurious 404s on R2).
                legacy_key = row.processed_image_r2_key
                if legacy_key:
                    already_handled = keys_map and isinstance(keys_map, dict) and legacy_key in keys_map.values()
                    if not already_handled:
                        _r2_delete(legacy_key, client)

            _run(
                db,
                "scan_debug",
                "DELETE FROM scan_debug WHERE purge_after < now()",
            )
            db.commit()


def purge_unknown_scans(Session, dry_run: bool) -> None:
    """
    Hard-delete label scans saved as store_status='unknown' older than 7 days.

    Part B retention — PII (user_lat / user_lng) lives on these rows only
    for the reconciliation window. Beyond 7d the scan cannot be retro-attached
    to any store via receipt upload anyway (DA-30), so we roll up counts by
    ISO week into unknown_scans_weekly_aggregate and drop the source rows.

    Both the aggregation and the delete happen inside a single transaction.
    """
    with Session() as db:
        cutoff = db.execute(text("SELECT now() - interval '7 days'")).scalar()

        aggregated = db.execute(
            text("""
                INSERT INTO unknown_scans_weekly_aggregate
                    (year_week, scan_count, count_per_scan_type, updated_at)
                SELECT
                    ywk AS year_week,
                    SUM(type_count)::int AS scan_count,
                    jsonb_object_agg(scan_type, type_count) AS count_per_scan_type,
                    now()
                FROM (
                    SELECT
                        to_char(scanned_at, 'IYYY-"W"IW') AS ywk,
                        scan_type,
                        COUNT(*) AS type_count
                    FROM scans
                    WHERE store_status = 'unknown'
                      AND scanned_at < :cutoff
                    GROUP BY to_char(scanned_at, 'IYYY-"W"IW'), scan_type
                ) per_week_type
                GROUP BY ywk
                ON CONFLICT (year_week) DO UPDATE SET
                    scan_count =
                        unknown_scans_weekly_aggregate.scan_count + EXCLUDED.scan_count,
                    count_per_scan_type =
                        unknown_scans_weekly_aggregate.count_per_scan_type
                        || EXCLUDED.count_per_scan_type,
                    updated_at = now()
            """),
            {"cutoff": cutoff},
        )
        log.info("unknown_scans_aggregate: %d week-row(s) upserted", aggregated.rowcount)

        _run(
            db,
            "unknown_scans purge",
            "DELETE FROM scans WHERE store_status = 'unknown' AND scanned_at < :cutoff",
            {"cutoff": cutoff},
        )
        if not dry_run:
            db.commit()


STEPS = [
    ("refresh_tokens", purge_refresh_tokens),
    ("optimized_routes", purge_optimized_routes),
    ("notification_logs", purge_notification_logs),
    ("user_sessions", aggregate_and_purge_sessions),
    ("photo_hashes", purge_photo_hashes),
    ("receipt_images", purge_receipt_images),
    ("label_images", purge_label_images),
    ("expire_community_challenges", expire_community_challenges),
    ("label_pending_orphans", purge_label_pending_orphans),
    ("unknown_scans", purge_unknown_scans),
    ("scan_debug", purge_scan_debug),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ratis daily purge batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log affected row counts without committing any changes",
    )
    args = parser.parse_args()

    # Init Sentry first — silent no-op when SENTRY_DSN is absent. Any
    # exception raised during purge steps is then captured.
    init_sentry("ratis_batch_purge")

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be committed")

    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("DATABASE_URL environment variable is not set")
        sys.exit(1)

    # R2 credentials are read lazily inside the receipt/label/scan-debug steps.
    # Validate them up-front (unless --dry-run, which never touches R2) so a
    # missing var fails fast instead of raising a KeyError mid-run after the
    # earlier steps have already committed.
    if not args.dry_run:
        require_env(
            "R2_ENDPOINT_URL",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET_NAME",
        )

    engine = make_engine(url, pool_pre_ping=True)
    Session = sessionmaker(engine)

    errors: list[str] = []
    for name, fn in STEPS:
        try:
            fn(Session, dry_run=args.dry_run)
        except Exception as exc:
            log.error("FAILED %s: %s", name, exc, exc_info=True)
            errors.append(name)

    status = "failed" if errors else "success"
    try:
        _write_sync_log(Session, status, args.dry_run)
    except Exception as exc:
        log.error("Failed to write sync log: %s", exc)

    if errors:
        log.error("Purge completed with errors: %s", ", ".join(errors))
        sys.exit(1)

    log.info("Purge completed successfully%s.", " (dry-run)" if args.dry_run else "")


if __name__ == "__main__":
    main()
