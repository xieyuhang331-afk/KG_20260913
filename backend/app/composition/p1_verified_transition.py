from app.modules.auth.manual_identity_review_adapter import (
    ManualIdentityReviewVerifiedTransitionService,
)
from app.modules.auth.manual_identity_review_application import (
    PlatformAdminManualIdentityReviewService,
)
from app.modules.auth.manual_identity_review_repository import (
    SqlAlchemyManualIdentityReviewAuthorityPort,
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


def create_platform_admin_manual_identity_review_service(
    *, session_factory, uuid_generator
) -> PlatformAdminManualIdentityReviewService:
    def authority_port_factory(**kwargs):
        return SqlAlchemyManualIdentityReviewAuthorityPort(
            session_factory=session_factory,
            **kwargs,
        )

    def transition_service_factory(authority_port):
        return create_manual_identity_review_verified_transition_service(
            authority_port=authority_port,
            verification_session_factory=session_factory,
            uuid_generator=uuid_generator,
        )

    return PlatformAdminManualIdentityReviewService(
        authority_port_factory=authority_port_factory,
        transition_service_factory=transition_service_factory,
        session_factory=session_factory,
        uuid_generator=uuid_generator,
    )
