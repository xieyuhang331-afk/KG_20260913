from __future__ import annotations

from sqlalchemy import func, or_, select, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.identity_submission_models import (
    IdentityVerificationSubmissionOrmModel,
)
from app.modules.auth.models import User


class SqlAlchemyIdentitySubmissionRepository:
    def __init__(self, session) -> None:
        self._session = session

    @staticmethod
    def _map_core() -> None:
        map_core_model_classes()

    async def lock_user(self, user_ref: int):
        self._map_core()
        result = await self._session.execute(
            select(User.id, User.role, User.status, User.tenant_id, User.verify_status)
            .where(User.id == user_ref).with_for_update()
        )
        return result.one_or_none()

    async def get_user(self, user_ref: int):
        self._map_core()
        result = await self._session.execute(
            select(User.id, User.role, User.status, User.tenant_id, User.verify_status)
            .where(User.id == user_ref).limit(1)
        )
        return result.one_or_none()

    async def find_by_idempotency(self, user_ref: int, digest: str):
        result = await self._session.execute(
            select(IdentityVerificationSubmissionOrmModel).where(
                IdentityVerificationSubmissionOrmModel.user_ref == user_ref,
                IdentityVerificationSubmissionOrmModel.idempotency_key_digest == digest,
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def find_latest(self, user_ref: int):
        result = await self._session.execute(
            select(IdentityVerificationSubmissionOrmModel)
            .where(IdentityVerificationSubmissionOrmModel.user_ref == user_ref)
            .order_by(IdentityVerificationSubmissionOrmModel.version.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def count_since(self, user_ref: int, since) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(IdentityVerificationSubmissionOrmModel).where(
                IdentityVerificationSubmissionOrmModel.user_ref == user_ref,
                IdentityVerificationSubmissionOrmModel.submitted_at >= since,
            )
        )
        return int(result.scalar_one())

    async def add(self, **values):
        real_name = values.pop("real_name")
        id_card = values.pop("id_card")
        model = IdentityVerificationSubmissionOrmModel(
            submission_id=values.pop("submission_id"),
            user_ref=values.pop("user_ref"), version=values.pop("version"),
            status="submitted",
            real_name_ciphertext=real_name.ciphertext,
            real_name_nonce=real_name.nonce,
            id_card_ciphertext=id_card.ciphertext,
            id_card_nonce=id_card.nonce,
            id_card_masked=values.pop("id_card_masked"),
            encryption_key_id=values.pop("key_id"),
            content_digest=values.pop("content_digest"),
            id_card_digest=values.pop("id_card_digest"),
            idempotency_key_digest=values.pop("idempotency_digest"),
            consent_version=values.pop("consent_version"),
            submitted_at=values.pop("submitted_at"),
        )
        self._session.add(model)
        await self._session.flush()
        return model

    async def mark_user_submitted(self, user_ref: int) -> None:
        self._map_core()
        result = await self._session.execute(
            update(User).where(
                User.id == user_ref,
                User.status == "active",
                User.role == "member",
                User.tenant_id.is_(None),
                or_(
                    User.verify_status.is_(None),
                    User.verify_status.in_(("pending", "rejected", "submitted")),
                ),
            ).values(verify_status="submitted")
        )
        if result.rowcount != 1:
            raise RuntimeError("identity submission user state changed")

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
