"""Celery app configuration tests.

Guards the OCR worker against unbounded runtime (DoS) : a hung or
adversarially-slow OCR task must be killed by Celery's soft/hard
time-limits rather than pinning a worker slot forever.
"""

from __future__ import annotations

from ratis_core.settings import load_settings


def test_celery_config_has_time_limits_from_settings():
    from celery_app import celery_app

    ocr = load_settings()["ocr"]
    soft = celery_app.conf.task_soft_time_limit
    hard = celery_app.conf.task_time_limit
    assert soft == ocr["task_soft_time_limit_s"]
    assert hard == ocr["task_time_limit_s"]
    # Soft limit must fire before the hard limit so the task gets a
    # chance to mark the receipt failed before SIGKILL.
    assert soft < hard


def test_celery_time_limits_not_hardcoded():
    """The limits must come from ratis_settings.json (R19), not literals."""
    from celery_app import celery_app

    ocr = load_settings()["ocr"]
    assert celery_app.conf.task_soft_time_limit == ocr["task_soft_time_limit_s"]
