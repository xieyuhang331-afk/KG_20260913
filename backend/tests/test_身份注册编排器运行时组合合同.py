from datetime import datetime, timezone
import asyncio

import pytest

from app.core.uuid_generator import Uuid7Generator


def _clock():
    return datetime.now(timezone.utc)


def test_身份注册编排器运行时组合尚未实现():
    try:
        from app.composition.registration_outbox_runtime import (
            RegistrationOutboxRuntimeComposition,
        )
    except ModuleNotFoundError:
        raise AssertionError(
            "Registration orchestrator runtime composition is not implemented"
        ) from None

    identity_session_factory = object()
    worker_session_factory = object()
    composition = RegistrationOutboxRuntimeComposition(
        identity_session_factory=identity_session_factory,
        worker_session_factory=worker_session_factory,
        clock=_clock,
        uuid_generator=Uuid7Generator(),
    )

    dispatcher = composition.create_dispatcher()
    reconciler = composition.create_reconciler()

    assert dispatcher._unit_of_work_factory is not None
    assert dispatcher._orchestrator is dispatcher._outcome_confirmer
    assert reconciler._unit_of_work_factory is dispatcher._unit_of_work_factory
    assert composition.identity_session_factory is identity_session_factory
    assert composition.worker_session_factory is worker_session_factory
    assert (
        composition._identity_persistence._session_factory
        is identity_session_factory
    )
    assert composition.worker_unit_of_work_factory()._session_factory is (
        worker_session_factory
    )


def test_运行时组合每次创建新鲜工作单元而不打开会话():
    from app.composition.registration_outbox_runtime import (
        RegistrationOutboxRuntimeComposition,
    )

    identity_opened = []
    worker_opened = []

    def identity_session_factory():
        identity_opened.append(True)
        raise AssertionError("composition must not open an identity session")

    def worker_session_factory():
        worker_opened.append(True)
        raise AssertionError("composition must not open a session")

    composition = RegistrationOutboxRuntimeComposition(
        identity_session_factory=identity_session_factory,
        worker_session_factory=worker_session_factory,
        clock=_clock,
        uuid_generator=Uuid7Generator(),
    )
    first = composition.worker_unit_of_work_factory()
    second = composition.worker_unit_of_work_factory()

    assert first is not second
    assert identity_opened == []
    assert worker_opened == []
    assert first._session_factory is worker_session_factory
    assert second._session_factory is worker_session_factory


def test_注册发件箱Dispatcher到编排器投递接线尚未实现():
    asyncio.run(_assert_dispatcher_delivery_wiring())


async def _assert_dispatcher_delivery_wiring():
    try:
        from app.composition.registration_outbox_runtime import (
            RegistrationOutboxDeliveryRuntime,
        )
    except ImportError:
        raise AssertionError(
            "Registration outbox dispatcher delivery wiring is not implemented"
        ) from None

    calls = []

    class Dispatcher:
        async def run_once(self, *, lease_owner, limit):
            calls.append(("dispatch", lease_owner, limit))
            return object()

    class Reconciler:
        async def run_once(self, *, limit):
            calls.append(("reconcile", limit))
            return 3

    class Composition:
        def create_dispatcher(self):
            calls.append(("create_dispatcher",))
            return Dispatcher()

        def create_reconciler(self):
            calls.append(("create_reconciler",))
            return Reconciler()

    runtime = RegistrationOutboxDeliveryRuntime(Composition())
    result = await runtime.dispatch_once(lease_owner="registration-worker-1")
    reconciled = await runtime.reconcile_once()

    assert result is not None
    assert reconciled == 3
    assert calls == [
        ("create_dispatcher",),
        ("dispatch", "registration-worker-1", 50),
        ("create_reconciler",),
        ("reconcile", 50),
    ]


def test_投递接线原样传播Cancellation且不复用Dispatcher():
    asyncio.run(_assert_cancellation_and_fresh_dispatcher())


async def _assert_cancellation_and_fresh_dispatcher():
    created = []

    class Dispatcher:
        async def run_once(self, **_kwargs):
            raise asyncio.CancelledError

    class Composition:
        def create_dispatcher(self):
            dispatcher = Dispatcher()
            created.append(dispatcher)
            return dispatcher

    from app.composition.registration_outbox_runtime import (
        RegistrationOutboxDeliveryRuntime,
    )

    runtime = RegistrationOutboxDeliveryRuntime(Composition())
    with pytest.raises(asyncio.CancelledError):
        await runtime.dispatch_once(lease_owner="registration-worker-1")
    with pytest.raises(asyncio.CancelledError):
        await runtime.dispatch_once(lease_owner="registration-worker-1")

    assert len(created) == 2
    assert created[0] is not created[1]
