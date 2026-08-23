import os

from celery import Celery
from kombu import Queue


REGISTRATION_QUEUE = "registration"
DISPATCH_TASK_NAME = "identity.registration.dispatch_outbox"
RECONCILE_TASK_NAME = "identity.registration.reconcile_outbox"
PRIVATE_FILE_QUEUE = "private-file"
PRIVATE_FILE_SCAN_TASK_NAME = "phase1.private_file.scan"
THERAPIST_WORKFLOW_QUEUE = "therapist-workflow"
MEMBER_ENROLLMENT_QUEUE = "member-enrollment-workflow"
SLICE4_HEALTH_QUEUE = "slice4-health-workflow"
_DECLARED_QUEUES = (
    "ai",
    "judgment",
    "ocr",
    "report",
    "settlement",
    "notification",
    REGISTRATION_QUEUE,
    PRIVATE_FILE_QUEUE,
    THERAPIST_WORKFLOW_QUEUE,
    MEMBER_ENROLLMENT_QUEUE,
    SLICE4_HEALTH_QUEUE,
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
        include=("app.tasks.registration_outbox_tasks", "app.tasks.institution_onboarding_tasks", "app.tasks.therapist_qualification_tasks", "app.tasks.member_enrollment_tasks", "app.tasks.slice4_health_data_tasks"),
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
            "phase1.therapist.dispatch_outbox": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.therapist.consume_outbox": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.therapist.recover_workflow": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.therapist.expire_qualifications": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.therapist.expire_invitations": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.therapist.recompute_readiness": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.therapist.sweep_readiness": {"queue": THERAPIST_WORKFLOW_QUEUE},
            "phase1.member_enrollment.dispatch_outbox": {"queue": MEMBER_ENROLLMENT_QUEUE},
            "phase1.member_enrollment.consume_outbox": {"queue": MEMBER_ENROLLMENT_QUEUE},
            "phase1.member_enrollment.recover_outbox": {"queue": MEMBER_ENROLLMENT_QUEUE},
            "phase1.member_enrollment.expire_invitations": {"queue": MEMBER_ENROLLMENT_QUEUE},
            "phase1.slice4.dispatch_outbox": {"queue": SLICE4_HEALTH_QUEUE},
            "phase1.slice4.consume_outbox": {"queue": SLICE4_HEALTH_QUEUE},
            "phase1.slice4.recover_outbox": {"queue": SLICE4_HEALTH_QUEUE},
            "phase1.slice4.recompute_readiness": {"queue": SLICE4_HEALTH_QUEUE},
            "phase1.slice4.sweep_readiness": {"queue": SLICE4_HEALTH_QUEUE},
            "phase1.slice4.build_projection_v2": {"queue": SLICE4_HEALTH_QUEUE},
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
            "therapist-workflow-dispatch": {
                "task": "phase1.therapist.dispatch_outbox",
                "schedule": 5.0,
                "options": {"queue": THERAPIST_WORKFLOW_QUEUE},
            },
            "therapist-workflow-recover": {
                "task": "phase1.therapist.recover_workflow",
                "schedule": 60.0,
                "options": {"queue": THERAPIST_WORKFLOW_QUEUE},
            },
            "therapist-invitation-expiry": {
                "task": "phase1.therapist.expire_invitations",
                "schedule": 60.0,
                "options": {"queue": THERAPIST_WORKFLOW_QUEUE},
            },
            "therapist-readiness-sweep": {
                "task": "phase1.therapist.sweep_readiness",
                "schedule": 60.0,
                "options": {"queue": THERAPIST_WORKFLOW_QUEUE},
            },
            "member-enrollment-dispatch": {
                "task": "phase1.member_enrollment.dispatch_outbox",
                "schedule": 5.0,
                "options": {"queue": MEMBER_ENROLLMENT_QUEUE},
            },
            "member-enrollment-recovery": {
                "task": "phase1.member_enrollment.recover_outbox",
                "schedule": 60.0,
                "options": {"queue": MEMBER_ENROLLMENT_QUEUE},
            },
            "member-enrollment-invitation-expiry": {
                "task": "phase1.member_enrollment.expire_invitations",
                "schedule": 60.0,
                "options": {"queue": MEMBER_ENROLLMENT_QUEUE},
            },
            "slice4-health-dispatch": {
                "task": "phase1.slice4.dispatch_outbox",
                "schedule": 5.0,
                "options": {"queue": SLICE4_HEALTH_QUEUE},
            },
            "slice4-health-recovery": {
                "task": "phase1.slice4.recover_outbox",
                "schedule": 60.0,
                "options": {"queue": SLICE4_HEALTH_QUEUE},
            },
            "slice4-readiness-sweep": {
                "task": "phase1.slice4.sweep_readiness",
                "schedule": 60.0,
                "options": {"queue": SLICE4_HEALTH_QUEUE},
            },
        },
    )
    return app


celery_app = create_celery_app()
