import os

from celery import Celery
from kombu import Queue


REGISTRATION_QUEUE = "registration"
DISPATCH_TASK_NAME = "identity.registration.dispatch_outbox"
RECONCILE_TASK_NAME = "identity.registration.reconcile_outbox"
PRIVATE_FILE_QUEUE = "private-file"
PRIVATE_FILE_SCAN_TASK_NAME = "phase1.private_file.scan"
_DECLARED_QUEUES = (
    "ai",
    "judgment",
    "ocr",
    "report",
    "settlement",
    "notification",
    REGISTRATION_QUEUE,
    PRIVATE_FILE_QUEUE,
)


def get_declared_queues() -> tuple[str, ...]:
    return _DECLARED_QUEUES


def create_celery_app(*, broker_url: str | None = None) -> Celery:
    resolved_broker = broker_url or os.getenv("KG_CELERY_BROKER_URL")
    test_result_backend = os.getenv("KG_CELERY_TEST_RESULT_BACKEND")
    app = Celery(
        "kg_registration",
        broker=resolved_broker or "fail://",
        backend="rpc://" if test_result_backend == "rpc" else None,
        include=("app.tasks.registration_outbox_tasks", "app.tasks.institution_onboarding_tasks"),
    )
    app.conf.update(
        accept_content=("json",),
        task_serializer="json",
        result_serializer="json",
        task_ignore_result=test_result_backend != "rpc",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        task_default_queue=REGISTRATION_QUEUE,
        task_queues=tuple(Queue(name) for name in _DECLARED_QUEUES),
        task_routes={
            DISPATCH_TASK_NAME: {"queue": REGISTRATION_QUEUE},
            RECONCILE_TASK_NAME: {"queue": REGISTRATION_QUEUE},
            PRIVATE_FILE_SCAN_TASK_NAME: {"queue": PRIVATE_FILE_QUEUE},
        },
        beat_schedule={
            "registration-dispatch": {
                "task": DISPATCH_TASK_NAME,
                "schedule": 5.0,
                "options": {"queue": REGISTRATION_QUEUE},
            },
            "registration-reconcile": {
                "task": RECONCILE_TASK_NAME,
                "schedule": 60.0,
                "options": {"queue": REGISTRATION_QUEUE},
            },
        },
    )
    return app


celery_app = create_celery_app()
