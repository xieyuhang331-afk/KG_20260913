from __future__ import annotations

from datetime import datetime
from typing import Protocol


class OnboardingRepositoryPort(Protocol):
    async def get_invitation_for_update(self, invitation_id: str): ...
    async def get_application_for_user(self, user_id: int): ...
    async def get_application_for_review(self, application_id: str): ...


class SecretCipherPort(Protocol):
    def encrypt(self, value: str) -> bytes: ...
    def decrypt(self, value: bytes) -> str: ...
    def digest(self, value: str) -> str: ...


class InstitutionApprovalDeliveryPort(Protocol):
    async def deliver(
        self,
        *,
        event_id: str,
        recipient_user_id: int,
        payload: dict,
        payload_digest: str,
        now: datetime,
    ): ...
