from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from celery import Celery
from celery.signals import worker_process_init
from ratis_core.settings import load_settings

# Module-level: Celery needs the broker URL at import time to build the app object.
REDIS_URL = os.environ["REDIS_URL"]

# OCR worker time-limits — bound task runtime so a hung or adversarially
# slow scan cannot pin a worker slot indefinitely (DoS). Sourced from
# ratis_settings.json (R19 — never hardcode). The soft limit raises
# ``SoftTimeLimitExceeded`` inside the task (caught by ``process_receipt``
# to mark the receipt failed) ; the hard limit SIGKILLs the worker as a
# last resort if the soft handler itself hangs.
_OCR_SETTINGS = load_settings()["ocr"]
_TASK_SOFT_TIME_LIMIT = _OCR_SETTINGS["task_soft_time_limit_s"]
_TASK_TIME_LIMIT = _OCR_SETTINGS["task_time_limit_s"]

celery_app = Celery(
    "ratis_product_analyser",
    broker=REDIS_URL,
    # Result backend reuses the broker — required so admin replay tasks
    # (cf. ARCH_admin_endpoints PR4) can be polled via AsyncResult by
    # ``GET /api/v1/admin/tasks/{task_id}/status``. Tasks default to
    # ``ignore_result=True`` (see ``conf.update`` below) so this only
    # adds Redis writes for tasks that explicitly opt in via
    # ``@celery_app.task(ignore_result=False)``.
    backend=REDIS_URL,
    include=[
        "worker.receipt_task",
        "worker.label_task",
        "worker.barcode_reparse_task",
        "worker.pipeline_replay_task",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,
    task_soft_time_limit=_TASK_SOFT_TIME_LIMIT,
    task_time_limit=_TASK_TIME_LIMIT,
)


@worker_process_init.connect
def _init_llm_observability(**_kwargs) -> None:
    """Initialise Langfuse LLM tracing POST-fork (DA-LO2).

    Celery uses the prefork pool : the OTEL export threads created by
    ``AnthropicInstrumentor().instrument()`` must be spawned in the child
    process, so we hook ``worker_process_init`` rather than initialising at
    module import time (pre-fork threads are dead in the child). No-op when
    the Langfuse keys are absent — see ``init_langfuse`` (RGPD-hardened).

    Sentry is intentionally NOT initialised here : the worker has no Sentry
    init today (only the FastAPI app does, ``main.py``), and this signal is
    scoped to the LLM-observability wiring (cf ARCH_llm_observability.md).
    """
    from ratis_core.observability import init_langfuse

    init_langfuse("ratis_product_analyser")
