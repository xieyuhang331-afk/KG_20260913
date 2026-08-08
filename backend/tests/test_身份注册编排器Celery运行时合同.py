import asyncio
from types import SimpleNamespace


def test_身份注册投递Celery运行时尚未实现():
    try:
        from app.tasks.registration_outbox_tasks import (
            dispatch_registration_outbox,
            reconcile_registration_outbox,
        )
    except (ImportError, ModuleNotFoundError):
        raise AssertionError(
            "Registration delivery Celery runtime is not implemented"
        ) from None

    assert dispatch_registration_outbox.name == (
        "identity.registration.dispatch_outbox"
    )
    assert reconcile_registration_outbox.name == (
        "identity.registration.reconcile_outbox"
    )


def test_Celery仅声明专用registration队列和冻结Beat周期():
    from app.tasks.celery_app import (
        DISPATCH_TASK_NAME,
        RECONCILE_TASK_NAME,
        REGISTRATION_QUEUE,
        celery_app,
    )

    queues = {queue.name for queue in celery_app.conf.task_queues}
    assert REGISTRATION_QUEUE == "registration"
    assert "registration" in queues
    assert celery_app.conf.task_routes[DISPATCH_TASK_NAME]["queue"] == (
        "registration"
    )
    assert celery_app.conf.task_routes[RECONCILE_TASK_NAME]["queue"] == (
        "registration"
    )
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.beat_schedule["registration-dispatch"]["schedule"] == 5.0
    assert celery_app.conf.beat_schedule["registration-reconcile"]["schedule"] == 60.0
    assert celery_app.conf.beat_schedule["registration-dispatch"]["options"] == {
        "queue": "registration"
    }


def test_Celery任务每次使用新鲜Runtime并仅返回结果计数():
    from app.tasks.registration_outbox_tasks import (
        _run_dispatch,
        _run_reconcile,
    )

    runtimes = []
    calls = []

    class Runtime:
        async def dispatch_once(self, *, lease_owner, limit):
            calls.append(("dispatch", lease_owner, limit))
            return SimpleNamespace(
                claimed=2,
                delivered=1,
                retried=1,
                review_required=0,
                dead_lettered=0,
                lease_lost=0,
            )

        async def reconcile_once(self, *, limit):
            calls.append(("reconcile", limit))
            return 4

    class Bootstrap:
        async def __aenter__(self):
            return factory()

        async def __aexit__(self, *_args):
            return False

    def factory():
        runtime = Runtime()
        runtimes.append(runtime)
        return runtime

    request = SimpleNamespace(hostname="registration-a@localhost")
    dispatch_result = asyncio.run(
        _run_dispatch(request, bootstrap_factory=Bootstrap)
    )
    reconcile_result = asyncio.run(
        _run_reconcile(bootstrap_factory=Bootstrap)
    )

    assert dispatch_result == {
        "worker_ref": calls[0][1],
        "claimed": 2,
        "delivered": 1,
        "retried": 1,
        "review_required": 0,
        "dead_lettered": 0,
        "lease_lost": 0,
    }
    assert reconcile_result == {"reconciled": 4}
    assert len(runtimes) == 2
    assert runtimes[0] is not runtimes[1]
    assert calls[0][0] == "dispatch"
    assert calls[0][2] == 50
    assert calls[1] == ("reconcile", 50)
    assert "@" not in calls[0][1]


def test_Celery任务原样传播Cancellation():
    from app.tasks.registration_outbox_tasks import (
        _run_dispatch,
    )

    class Runtime:
        async def dispatch_once(self, **_kwargs):
            raise asyncio.CancelledError

    class Bootstrap:
        async def __aenter__(self):
            return Runtime()

        async def __aexit__(self, *_args):
            return False

    try:
        asyncio.run(
            _run_dispatch(
                SimpleNamespace(hostname="registration-a@localhost"),
                bootstrap_factory=Bootstrap,
            )
        )
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("Cancellation must propagate")
