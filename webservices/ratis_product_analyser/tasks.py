from __future__ import annotations

import uuid


def enqueue_ocr_job(receipt_id: uuid.UUID) -> None:
    """Enqueue an OCR processing job for the given receipt."""
    from celery_app import celery_app  # late import — avoids circular import at test time

    celery_app.send_task("worker.receipt_task.process_receipt", args=[str(receipt_id)])


def enqueue_label_job(scan_id: uuid.UUID, hint: str = "label") -> None:
    """Enqueue an OCR processing job for a single label scan."""
    from celery_app import celery_app  # late import — avoids circular import at test time

    celery_app.send_task("worker.label_task.process_label", args=[str(scan_id)], kwargs={"hint": hint})
