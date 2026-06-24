"""Re-enqueue OCR jobs for receipts whose total_amount is still NULL.

Useful after a worker outage / crash where the original Celery tasks were
dropped before completing. Runs inside the product_analyser container so
it has access to the same DB + Redis broker.

Usage:
    docker cp tools/reenqueue_pending_ocr.py ratis-product_analyser-1:/tmp/
    docker compose ... exec -T product_analyser python /tmp/reenqueue_pending_ocr.py
"""

import os

import psycopg

DB_URL = os.environ["DATABASE_URL"]


def find_pending_receipts() -> list[str]:
    sync_url = DB_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT id::text FROM receipts
                WHERE total_amount IS NULL
                  AND image_uploaded_at IS NOT NULL
                  AND image_deleted_at IS NULL
                ORDER BY created_at DESC
                """
        )
        return [row[0] for row in cur.fetchall()]


def main() -> None:
    pending = find_pending_receipts()
    print(f"Found {len(pending)} receipts pending OCR:")
    for rid in pending:
        print(f"  {rid}")

    if not pending:
        print("Nothing to re-enqueue.")
        return

    from celery_app import celery_app

    for rid in pending:
        celery_app.send_task("worker.receipt_task.process_receipt", args=[rid])
        print(f"  enqueued {rid}")

    print(f"Done — {len(pending)} tasks back in the queue.")


if __name__ == "__main__":
    main()
