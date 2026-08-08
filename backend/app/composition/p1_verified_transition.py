from app.modules.auth.manual_identity_review_adapter import (
    ManualIdentityReviewVerifiedTransitionService,
)
from app.modules.auth.registration_outbox import (
    P1VerificationTransitionWriter,
)
from app.modules.auth.registration_outbox_repository import (
    SqlAlchemyP1VerificationTransitionUnitOfWork,
)


def create_manual_identity_review_verified_transition_service(
    *,
    authority_port,
    verification_session_factory,
    uuid_generator,
) -> ManualIdentityReviewVerifiedTransitionService:
    writer = P1VerificationTransitionWriter(
        unit_of_work_factory=lambda: (
            SqlAlchemyP1VerificationTransitionUnitOfWork(
                verification_session_factory
            )
        ),
        uuid_generator=uuid_generator,
    )
    return ManualIdentityReviewVerifiedTransitionService(
        authority_port=authority_port,
        transition_writer=writer,
    )

