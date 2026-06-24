"""Celery application for ratis_list_optimiser background tasks."""

from __future__ import annotations

import os

from celery import Celery
from ratis_core.startup import require_env


def validate_worker_env() -> None:
    """Fail fast if the worker is missing a required env var.

    The web process validates these in its FastAPI lifespan (``main.py``),
    but the Celery worker has no lifespan — without this check the first
    task crashes mid-flight instead of failing visibly at deploy time
    (cousin of KP-83). Called at module load so an under-configured
    worker container refuses to start.
    """
    require_env("DATABASE_URL", "REDIS_URL", "OSRM_BASE_URL", "JWT_PUBLIC_KEY_PATH")


validate_worker_env()

_redis_url = os.environ["REDIS_URL"]

celery_app = Celery(
    "ratis_list_optimiser",
    broker=_redis_url,
    backend=_redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_prefetch_multiplier=1,
    include=["worker.tasks"],
)
