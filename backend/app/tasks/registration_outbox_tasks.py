import asyncio
import hashlib

from .celery_app import (
    DISPATCH_TASK_NAME,
    RECONCILE_TASK_NAME,
    celery_app,
)
from .registration_outbox_worker_bootstrap import (
    RegistrationOutboxWorkerBootstrap,
)


def _environment_bootstrap():
    return RegistrationOutboxWorkerBootstrap.from_environment()


def _lease_owner(request) -> str:
    identity = str(getattr(request, "hostname", ""))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"registration-worker:{digest}"


@celery_app.task(
    bind=True,
    name=DISPATCH_TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_registration_outbox(self):
    return asyncio.run(_run_dispatch(self.request))


async def _run_dispatch(request, *, bootstrap_factory=_environment_bootstrap):
    lease_owner = _lease_owner(request)
    async with bootstrap_factory() as runtime:
        summary = await runtime.dispatch_once(
            lease_owner=lease_owner, limit=50
        )
    return {
        "worker_ref": lease_owner,
        "claimed": summary.claimed,
        "delivered": summary.delivered,
        "retried": summary.retried,
        "review_required": summary.review_required,
        "dead_lettered": summary.dead_lettered,
        "lease_lost": summary.lease_lost,
    }


@celery_app.task(
    bind=True,
    name=RECONCILE_TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconcile_registration_outbox(self):
    return asyncio.run(_run_reconcile())


async def _run_reconcile(*, bootstrap_factory=_environment_bootstrap):
    async with bootstrap_factory() as runtime:
        reconciled = await runtime.reconcile_once(limit=50)
    return {"reconciled": reconciled}
