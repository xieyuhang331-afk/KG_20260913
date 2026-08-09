from app.modules.auth.identity_submission import IdentitySubmissionService
from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto
from app.modules.auth.identity_submission_repository import (
    SqlAlchemyIdentitySubmissionRepository,
)


def create_identity_submission_service(session) -> IdentitySubmissionService:
    return IdentitySubmissionService(
        repository=SqlAlchemyIdentitySubmissionRepository(session),
        crypto=IdentitySubmissionCrypto.from_environment(),
    )
