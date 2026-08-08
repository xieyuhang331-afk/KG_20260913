import secrets

from app.modules.auth.registration_outbox_worker import (
    RegistrationOutboxDispatcher,
    RegistrationOutboxReconciler,
)
from app.modules.auth.registration_outbox_worker_repository import (
    SqlAlchemyRegistrationOutboxWorkerUnitOfWork,
)

from .identity_persistence import (
    IdentityPersistenceComposition,
    create_registration_orchestrator,
)


class RegistrationOutboxRuntimeComposition:
    """Compose one registration delivery runtime without opening resources."""

    def __init__(
        self,
        *,
        identity_session_factory,
        worker_session_factory,
        clock,
        uuid_generator,
        random_bits=secrets.randbits,
    ) -> None:
        self.identity_session_factory = identity_session_factory
        self.worker_session_factory = worker_session_factory
        self._identity_persistence = IdentityPersistenceComposition(
            identity_session_factory, clock
        )
        self._orchestrator = create_registration_orchestrator(
            identity_persistence=self._identity_persistence,
            uuid_generator=uuid_generator,
            random_bits=random_bits,
        )
        self._worker_unit_of_work_factory = (
            self.worker_unit_of_work_factory
        )

    def worker_unit_of_work_factory(
        self,
    ) -> SqlAlchemyRegistrationOutboxWorkerUnitOfWork:
        return SqlAlchemyRegistrationOutboxWorkerUnitOfWork(
            self.worker_session_factory
        )

    def create_dispatcher(self) -> RegistrationOutboxDispatcher:
        return RegistrationOutboxDispatcher(
            unit_of_work_factory=self._worker_unit_of_work_factory,
            orchestrator=self._orchestrator,
            outcome_confirmer=self._orchestrator,
        )

    def create_reconciler(self) -> RegistrationOutboxReconciler:
        return RegistrationOutboxReconciler(
            unit_of_work_factory=self._worker_unit_of_work_factory
        )
